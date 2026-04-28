# Submission Tracking

**Status**: DRAFT — design decisions captured, open questions remain.

## Purpose

Track IBS submission requests (SR) and release requests (RR) as entities
parallel to the package affectedness status, giving Vulnerability Analysts
visibility into the progression of fixes without altering the existing
`PackageStatus` model.

Today the codestream status jumps directly from `AFFECTED` to `RELEASED`
with no visibility into what happens in between. A maintainer may have
already submitted a fix days ago, but the VA has no way to know until the
fix lands in the codestream project (detected by `CodestreamReleaseDetector`
or `IBSEventConsumer`). This feature fills that gap by tracking both SRs
and RRs and showing them alongside the codestream affectedness status.

## Background: SUSE Maintenance Update Process

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

- **The incident is an implicit concept**: in STAMP's data model, the
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
STAMP must handle this by transitioning the record back to `open` state.

## Design Principle: Parallel Tracking

SRs and RRs are tracked as **separate entities** with their own lifecycle,
not as modifications to `PackageStatus`. The codestream remains `AFFECTED`
until the fix is actually released — SR/RR status is purely informational.

**Why not a new PackageStatus value?** Adding an intermediate status (e.g.,
`FIX_IN_PROGRESS`) between `AFFECTED` and `RELEASED` was considered and
rejected because:

- It would impact the ticket gate logic (is the new status final?
  non-final? does it block progression?)
- It would complicate the codestream-to-product propagation rules
- It would require handling regression (submission declined -> revert to
  `AFFECTED`)
- The PackageStatus enum is shared between codestream and product levels,
  but submissions only exist at the codestream level

**Why SR + RR instead of SR + MaintenanceIncident?** The VA's mental model
is centered on requests: "is there an SR? what state is it in? is there a
RR? what state is it in?". The incident is important as a linking concept
but not as the primary entity the VA interacts with. Modeling SR and RR as
first-class entities directly reflects the VA's perspective and avoids
introducing a derived "phase" enum that would need to be kept in sync with
the underlying request states.

## Data Model

Three new tables. No modifications to existing tables.

### SubmissionRequest

Tracks an IBS submission request (type `maintenance_incident`) relevant
to STAMP.

| Column             | Type         | Constraints          | Description                              |
|--------------------|--------------|----------------------|------------------------------------------|
| id                 | UUID         | PK                   | Internal identifier                      |
| request_number     | INTEGER      | UNIQUE, NOT NULL     | IBS request number (from payload `number`) |
| package_name       | VARCHAR      | NOT NULL             | Target package (from payload `actions[0].targetpackage`) |
| codestream_name    | VARCHAR      | NOT NULL             | Target codestream (from payload `actions[0].target_releaseproject`) |
| state              | ENUM         | NOT NULL, DEFAULT open | See SubmissionRequestState below        |
| author             | VARCHAR      |                      | IBS username who created the request (from payload `author`) |
| incident_number    | INTEGER      | nullable             | Populated when state becomes `accepted` (extracted from `actions[0].targetproject` which becomes `SUSE:Maintenance:XXXXX` after acceptance) |
| superseded_by      | INTEGER      | nullable             | Request number of the superseding request (from payload `superseded_by`) |
| created_at         | TIMESTAMP    | NOT NULL, DEFAULT    | Record creation timestamp                |
| updated_at         | TIMESTAMP    | NOT NULL, DEFAULT    | Record update timestamp                  |

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
STAMP.

| Column             | Type         | Constraints          | Description                              |
|--------------------|--------------|----------------------|------------------------------------------|
| id                 | UUID         | PK                   | Internal identifier                      |
| request_number     | INTEGER      | UNIQUE, NOT NULL     | IBS request number (from payload `number`) |
| package_name       | VARCHAR      | NOT NULL             | Target package (from payload `actions[0].targetpackage`) |
| codestream_name    | VARCHAR      | NOT NULL             | Target codestream (from payload `actions[0].targetproject`) |
| state              | ENUM         | NOT NULL, DEFAULT open | See ReleaseRequestState below           |
| incident_number    | INTEGER      | NOT NULL             | Extracted from `actions[0].sourceproject` (e.g., `SUSE:Maintenance:12345` -> `12345`) |
| created_at         | TIMESTAMP    | NOT NULL, DEFAULT    | Record creation timestamp                |
| updated_at         | TIMESTAMP    | NOT NULL, DEFAULT    | Record update timestamp                  |

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

### SubmissionRequestCodestream (Join Table)

Links a `SubmissionRequest` to the specific `TicketPackageCodestream`
records whose CVEs are mentioned in the request's diff.

| Column                        | Type      | Constraints                                | Description                        |
|-------------------------------|-----------|--------------------------------------------|------------------------------------|
| id                            | UUID      | PK                                         | Internal identifier                |
| submission_request_id         | UUID      | FK(submission_request.id), NOT NULL        | Related submission request         |
| ticket_package_codestream_id  | UUID      | FK(ticket_package_codestream.id), NOT NULL | Related codestream record          |
| created_at                    | TIMESTAMP | NOT NULL, DEFAULT                          | Record creation timestamp          |

**Unique constraint**: (submission_request_id, ticket_package_codestream_id)

### Why Explicit Correlation (Join Table)

Implicit matching (querying `TicketPackageCodestream` by codestream_name +
package_name at display time) was considered and rejected because it would
create **false positives**: if package `curl` on `SLE-15-SP6:Update` is
tracked by 3 tickets (CVE-A, CVE-B, CVE-C) but a submission only fixes
CVE-A, implicit matching would show the submission on all 3 tickets.

The join table ensures that a submission is only shown on tickets whose
CVEs are actually mentioned in the request's diff (changelog).

### Why No Join Table for ReleaseRequest

The RR does not need its own join table. Its correlation to tickets is
derived from the SR: given a `TicketPackageCodestream`, find the correlated
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

**Key fields for STAMP**:

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
RR payloads may contain `patchinfo` actions. STAMP must filter these and
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
and `bs_request_action_maintenance_incident.rb#execute_accept`). STAMP
extracts the `incident_number` from this field.

**State change events are only emitted for conclusive (final) state
transitions** (`bs_request.rb#send_state_change`): accepted, declined,
revoked, superseded. Transitions like `new -> review` or `declined -> new`
(reopen) do NOT emit `state_change` events. This means STAMP cannot detect
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

#### Request Details

```
GET /request/{number}
```

Returns full details of a single request, including current state and
action list. Used by the fetcher to check the current state of requests
that are `open` in STAMP but no longer appear in the search results
(because they transitioned to accepted/declined/revoked/superseded).

### IBS Diff API (CVE Correlation)

To correlate a submission request with specific CVEs, STAMP needs to
extract the CVE-IDs from the request's diff. Two potential approaches:

#### Option A: Request Diff Endpoint

```
POST /request/{id}?cmd=diff&withissues=1&view=xml
```

Operates directly on the request — no need to know source/target MD5s.
Available in OBS (see `request_controller.rb#request_command_diff`).

#### Option B: Source Diff Endpoint (existing)

```
POST /source/{project}/{package}?cmd=diff&view=xml&onlyissues=1&orev={old_md5}&rev={new_md5}
```

Same endpoint used by `CodestreamReleaseDetector`. Requires knowing the
MD5 checksums, which are not present in the RabbitMQ event payload —
would need an additional API call to obtain them.

**Preferred**: Option A (request diff) is simpler because it does not
require MD5 resolution. However, it has NOT been verified that
`withissues=1` on the request diff returns the same structured `<issues>`
output as the source diff. See Open Questions.

## Processing Pipelines

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
   TicketPackageCodestream in ANALYSIS or AFFECTED? If no -> skip
5. Is package_name tracked in at least one ticket for that
   codestream? If no -> skip
6. Create SubmissionRequest record (state = open)
7. Enqueue Celery task: correlate_submission_request(submission_id)
```

#### Celery Task: `correlate_submission_request`

```
1. Call IBS diff API for the request (see Data Sources above)
2. Extract CVE-IDs from the diff response
3. If no CVE-IDs found -> delete the SubmissionRequest (silent discard)
4. For each CVE-ID:
   a. Find the ticket with that CVE
   b. Find the TicketPackageCodestream for (ticket, codestream, package)
   c. Create SubmissionRequestCodestream join record
5. If no correlations were created (CVE-IDs found but no matching
   tickets) -> delete the SubmissionRequest
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
4. Does a SubmissionRequest with this incident_number exist in STAMP?
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
4. If not found -> ignore (not relevant to STAMP)
5. Map IBS state to STAMP state:
   - "accepted" -> accepted
     - Extract incident_number from actions[0].targetproject
       (now "SUSE:Maintenance:XXXXX")
     - Set SR.incident_number = extracted number
   - "declined" -> declined
   - "revoked" -> revoked
   - "superseded" -> superseded
     - Set SR.superseded_by from payload if available
6. Update SR.state

For RRs:
3. Find ReleaseRequest by request_number
4. If not found -> ignore (not relevant to STAMP)
5. Map IBS state to STAMP state:
   - "accepted" -> accepted
   - "declined" -> declined
   - "revoked" -> revoked
6. Update RR.state
```

**Note on reopens**: IBS does not emit `state_change` events for
non-conclusive transitions (e.g., `declined -> new`). If a declined SR
or RR is reopened, STAMP will not detect this via RabbitMQ. The catch-up
fetcher handles this case (see Pipeline 2).

### Pipeline 2: Periodic Catch-Up (RequestSyncFetcher)

A `BaseFetcher` subclass that runs periodically to recover events missed
during consumer downtime and reconcile state drift. This is the only
mechanism for detecting request reopens (declined -> new/review).

#### Procedure

```
Step 1 — Discover missed SRs and reconcile known ones:

  1. Identify active codestreams (distinct codestream_name values from
     TicketPackageCodestream records with status ANALYSIS or AFFECTED)

  2. For each active codestream:
     GET /request?view=collection&project={codestream}&states=new,review

     For each request in the response:
       a. Determine type from action: maintenance_incident (SR) or
          maintenance_release (RR)

       For SRs:
       b. Filter: is the targetpackage tracked in at least one ticket
          for this codestream? If no -> skip
       c. If NOT present in SubmissionRequest table:
          -> Create SubmissionRequest (state = open)
          -> Enqueue correlate_submission_request task
       d. If ALREADY present in SubmissionRequest but state is
          'declined':
          -> Update state to 'open' (the SR was reopened and STAMP
             missed the event)

       For RRs:
       e. If NOT present in ReleaseRequest table:
          -> Does a SubmissionRequest with this incident_number exist?
             If no -> skip
          -> Create ReleaseRequest (state = open)
       f. If ALREADY present in ReleaseRequest but state is 'declined':
          -> Update state to 'open' (the RR was reopened)

Step 2 — Reconcile requests no longer in new/review:

  3. Query all SubmissionRequest records with state = 'open' that were
     NOT seen in Step 1 (no longer in new/review state in IBS)

  4. For each such record:
     GET /request/{number}
     -> Update state to the current IBS state (accepted, declined,
       revoked, superseded)
     -> If accepted: extract incident_number from the response

  5. Query all ReleaseRequest records with state = 'open' that were
     NOT seen in Step 1

  6. For each such record:
     GET /request/{number}
     -> Update state to the current IBS state (accepted, declined,
       revoked)
```

#### Why This Approach

The catch-up problem for request tracking differs from the
`package.commit` catch-up (handled by `CodestreamReleaseDetector`):

- For `package.commit`, the MD5 checksum cache
  (`CodestreamPackageChecksum`) provides a "known good state" to diff
  against — any MD5 change since the last check is detectable regardless
  of missed events.
- For requests, there is no equivalent "state to compare" — a request
  created and accepted during downtime leaves no trace in STAMP's local
  state.
- Additionally, IBS does not emit `state_change` events for reopens
  (`declined -> new/review`), making the catch-up fetcher the only
  mechanism for detecting them.

The IBS Request Search API (`GET /request?view=collection`) fills this
gap by allowing STAMP to query for currently open requests. Combined with
point-lookup for known requests (`GET /request/{number}`), this covers
missed creations, missed state changes, and reopens.

**Volume estimate**: with ~20-30 active codestreams and ~30 open requests
per codestream, Step 1 processes ~600-900 requests per run. Most will
already be known to STAMP (local DB lookup, fast). Only genuinely new
requests trigger a diff API call. Step 2 processes only requests in `open`
state in STAMP that were not seen in Step 1 — typically a small number.

## UI Requirements

### Visualization Format

In the ticket detail view, within the affectedness tree, each codestream
in `AFFECTED` (or `ANALYSIS`) status shows the update progression as a
chain:

```
SR#XXXXX → SM#XXXXX → RR#XXXXX
```

Each element is a hyperlink:
- **SR#XXXXX** — `https://build.suse.de/request/show/XXXXX`
- **SM#XXXXX** — `https://build.suse.de/project/show/SUSE:Maintenance:XXXXX`
- **RR#XXXXX** — `https://build.suse.de/request/show/XXXXX`

The arrows (`→`) indicate progression through the MU process. Elements
that do not yet exist are not shown — the chain grows as the process
advances.

### Color Coding

Colors indicate the state of each element:

| Color  | Meaning                        | Applies to |
|--------|--------------------------------|------------|
| Yellow | In review (IBS `new`/`review`) | SR, RR     |
| Green  | Accepted                       | SR, RR     |
| Red    | Declined or revoked            | SR, RR     |
| Orange | Superseded                     | SR         |
| Grey   | No state (neutral)             | SM         |

The incident (SM) is always grey — it has no state of its own.

### Display Logic

The chain shown for a given ticket/codestream/package is determined by:

1. **If an incident exists** (at least one accepted SR with an
   `incident_number`): show the chain based on the **most recent
   incident**:
   - SR = the most recent SR accepted into that incident
   - SM = the incident
   - RR = the most recent RR for that incident (if any)

2. **If no incident exists**: show the **most recent SR** (pending or
   declined, with no incident or RR in the chain).

New SRs that are pending but not yet associated with an active incident
are **not shown** while an incident chain is active. They appear in the
chain only after the UM accepts them into the incident.

### Examples

```
Package: curl                                         [Remove]
+-- SUSE:SLE-15-SP6:Update        [Affected]
|   |  SR#407175 → SM#43894 → RR#407225
|   +-- SLES 15 SP6               Affected   (eligible)
|   +-- SLED 15 SP6               Affected   (eligible)
+-- SUSE:SLE-15-SP5:Update        [Affected]
|   |  SR#407180
|   +-- ...
+-- SUSE:SLE-15-SP4:Update        [Released]
    +-- ...
```

The VA sees immediately:
- SP6: SR accepted (green), incident created (grey), RR in QA (yellow)
- SP5: SR pending (yellow), no incident yet
- SP4: released, no chain shown

### Progression Examples

```
Phase 1 — SR just created:
  SR#407175                              (yellow)

Phase 2 — SR accepted, incident created:
  SR#407175 → SM#43894                   (green → grey)

Phase 3 — RR created, in QA:
  SR#407175 → SM#43894 → RR#407225       (green → grey → yellow)

Phase 4 — RR accepted, released:
  SR#407175 → SM#43894 → RR#407225       (green → grey → green)
  (codestream will transition to RELEASED shortly)

SR declined, no incident:
  SR#407175                              (red)

RR declined, revoked, new RR created:
  SR#407175 → SM#43894 → RR#407230       (green → grey → yellow)
  (shows the most recent RR, previous revoked RR not shown)

SR superseded:
  SR#407180                              (orange → shows superseding SR)
  (or the superseding SR is shown if it exists in STAMP)
```

### General Rules

- The chain is **read-only** — no VA interaction required
- The chain is only shown for non-final codestream statuses (`ANALYSIS`,
  `AFFECTED`). Released codestreams do not show the chain (though the
  data remains in the database).
- All elements are clickable links to IBS

## API Endpoints

TBD — at minimum:
- An endpoint to list SRs correlated to a ticket or to a specific
  `TicketPackageCodestream`
- An endpoint to list RRs associated with a ticket (derived via
  SR.incident_number)

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
  (coodpool team packages only). STAMP consumes IBS events directly for
  universal coverage.
- **Product-level tracking**: SRs and RRs are tracked at the codestream
  level only. Products inherit the codestream fix when released.
- **Multi-codestream SRs**: while technically possible in IBS, SUSE
  convention for security updates requires one package + one codestream
  per SR. STAMP assumes this convention and processes only `actions[0]`.

## Impact on Existing Specifications

When this spec is finalized and moved to `docs/features/`, the following
documents will need updates:

- `docs/data-model.md` — add `SubmissionRequest`, `ReleaseRequest`,
  `SubmissionRequestState`, `ReleaseRequestState`, and
  `SubmissionRequestCodestream` tables
- `docs/features/ibs-rabbitmq-integration.md` — document new routing keys
  (`request.create`, `request.state_change`), new processing pipelines
  for SR and RR events, and shared consumer architecture
- `docs/features/obs-integration.md` — document IBS request search API
  (`GET /request?view=collection`), request detail API
  (`GET /request/{number}`), and request diff API
  (`POST /request/{id}?cmd=diff`)
- `docs/architecture.md` — mention the new fetcher and submission tracking
  in the system overview
- `docs/api-spec.md` — new endpoints for SR/RR data
- `docs/features/fetcher-dashboard.md` — the new `RequestSyncFetcher`
  appears in the dashboard

## Resolved Questions

### Request Diff API Output — VERIFIED

`POST /request/{id}?cmd=diff&withissues=1&view=xml` has been tested on
IBS with SR#407175 (freerdp maintenance request). Results:

- **Confirmed**: returns `<issues>` block with structured issue entries
- **Format**: `<issue state="added|changed" tracker="cve" name="2026-XXXXX"
  label="CVE-2026-XXXXX" url="..."/>` — identical structure to the source
  diff endpoint
- **Also includes**: bugzilla references (`tracker="bnc"`)
- **`withissues=1`** works correctly (includes issues alongside the diff
  output, unlike `onlyissues=1` which returns only issues)

**Decision**: use `POST /request/{id}?cmd=diff&withissues=1&view=xml` for
CVE correlation. No need for MD5 resolution or the source diff endpoint.

### IBS RabbitMQ Payload — VERIFIED

A test consumer connected to `amqps://suse:suse@rabbit.suse.de:5671` on
exchange `pubsub` captured 17 events over ~2.5 hours (2026-04-24).

**Routing keys confirmed**:
- `suse.obs.request.create` — binds and receives events
- `suse.obs.request.state_change` — binds and receives events

**SR state_change payload captured** (SR#407526, maintenance_incident,
declined):

```json
{
  "author": "JonathanKang",
  "comment": "Declined by auto-review script (check comments for details)",
  "description": "add CVE reference number",
  "id": 1083132,
  "number": 407526,
  "actions": [
    {
      "action_id": 3465663,
      "type": "maintenance_incident",
      "sourceproject": "SUSE:Maintenance:REQUEST:407526",
      "sourcepackage": "PackageKit.SUSE_SLE-15-SP5_Update",
      "sourcerevision": "a76248845dedfcfc34d98e0971370442",
      "targetproject": "SUSE:Maintenance",
      "target_releaseproject": "SUSE:SLE-15-SP5:Update",
      "makeoriginolder": false
    },
    {
      "action_id": 3465666,
      "type": "delete",
      "targetproject": "SUSE:Maintenance:REQUEST:407526",
      "makeoriginolder": false
    }
  ],
  "state": "declined",
  "oldstate": "review",
  "when": "2026-04-24T15:05:02",
  "who": "maintenance-robot",
  "namespace": "SUSE",
  "duration": 3842
}
```

**RR state_change payload captured** (RR#407226, maintenance_release,
accepted):

```json
{
  "author": "crazybyte",
  "comment": "Auto accept",
  "description": "requesting release",
  "id": 1082232,
  "number": 407226,
  "actions": [
    {
      "action_id": 3461979,
      "type": "maintenance_release",
      "sourceproject": "SUSE:Maintenance:43905",
      "sourcepackage": "PackageKit.SUSE_SLE-15-SP5_Update",
      "sourcerevision": "b4c1944feee5d3016fbd0ecddf227f7f",
      "targetproject": "SUSE:SLE-15-SP5:Update",
      "targetpackage": "PackageKit.43905",
      "makeoriginolder": false
    }
  ],
  "state": "accepted",
  "oldstate": "new",
  "when": "2026-04-24T14:34:25",
  "who": "darix",
  "namespace": "SUSE:SLE-15-SP5:Update",
  "duration": 172317
}
```

**Key observations from captured payloads**:

1. **`target_releaseproject` confirmed present in SR payloads** — value
   is the codestream (e.g., `SUSE:SLE-15-SP5:Update`). NOT present in
   RR payloads (not needed — codestream is in `targetproject` for RRs).
2. **`targetpackage` NOT present in SR payloads** — the package name
   must be extracted from `sourcepackage` by stripping the codestream
   suffix (`.SUSE_SLE-15-SP5_Update` -> `PackageKit`).
3. **`targetpackage` present in RR payloads but with incident suffix** —
   `PackageKit.43905` instead of `PackageKit`. Must strip `.XXXXX`.
4. **Spurious actions present** — SR payloads contain a `delete` action
   for the temporary project `SUSE:Maintenance:REQUEST:XXXXX`. RR
   payloads may contain `patchinfo` actions. Must filter by action type.
5. **`author`** is the original request creator; **`who`** is the person
   who performed the state change (e.g., reviewer, UM).
6. **`targetproject` in declined SR remains generic** —
   `SUSE:Maintenance` (not updated to incident). Confirms that
   `targetproject` is only updated to `SUSE:Maintenance:XXXXX` when the
   SR is accepted (per OBS source code analysis). This specific case
   (accepted SR) has not been captured on wire but is confirmed by code.
7. **All action field keys observed on wire**: `action_id`, `type`,
   `sourceproject`, `sourcepackage`, `sourcerevision`, `targetproject`,
   `targetpackage`, `target_releaseproject`, `makeoriginolder`.
   Fields `targetrepository` and `sourceupdate` (present in OBS source
   `notify_params`) were NOT observed in any captured event.

### Catch-Up Fetcher Frequency — DECIDED

Every 12 hours. This balances IBS API load with acceptable recovery time.
Reopens are rare and even if missed by the fetcher, the subsequent
conclusive state change (accepted/declined/revoked) will be caught by
the RabbitMQ consumer.

### Retention Policy — DECIDED

Forever. The data volume is small (a few records per ticket) and
retaining the full history provides value for auditing and analysis.

### Relationship with Release Detection — DECIDED

The two mechanisms remain independent. When `CodestreamReleaseDetector`
or `IBSEventConsumer` detects a codestream release, it does NOT update
`ReleaseRequest` records. The RR state is updated exclusively by:
- The RabbitMQ consumer (real-time, for conclusive state changes)
- The catch-up fetcher (periodic, for missed events)

### State Change Events and Reopens — DECIDED

Reopens (`declined -> new/review`) are rare in practice. Even if STAMP
does not detect the reopen in real-time, the subsequent conclusive state
change (the request is eventually accepted, declined again, or revoked)
will be caught by the RabbitMQ consumer. The 12-hour catch-up fetcher
provides an additional safety net.

Whether `state_change` events are emitted for non-conclusive transitions
remains to be verified empirically, but the design does not depend on it.

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
STAMP can fall back to querying `GET /request/{number}` to obtain the
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

### 3. Unknown CVE-IDs in Submission Request Diffs

When `correlate_submission_request` extracts CVE-IDs from the diff and
a CVE-ID is not known to STAMP (no CVE record, no ticket), the current
pipeline silently skips that CVE-ID and only creates join records for
known tickets.

This may contradict the project-wide policy for unknown CVE-IDs at the
codestream level (see `docs/features/package-tracking.md`, section
"Codestream Match Outcomes", Case C), which creates a CVE record and a
ticket via `create_ticket_from_detection`.

However, applying Case C here introduces a trade-off:

- **False positive risk**: a maintainer makes a typo in the changelog
  (e.g., `CVE-2026-99999` instead of `CVE-2026-9999`). The diff API
  returns the erroneous CVE-ID, and STAMP creates a spurious ticket for
  a non-existent CVE. This is a sporadic human error.
- **False negative risk**: a maintainer proactively submits a fix for a
  CVE that is not yet in NVD (e.g., under embargo or recently assigned).
  Ignoring the unknown CVE-ID means STAMP misses a legitimate early
  signal.

**Decision pending**: should `correlate_submission_request` invoke
`create_ticket_from_detection` for unknown CVE-IDs (consistent with
Case C) or silently skip them (conservative, avoids false positives)?
This is independent of Open Question 5 (diff API accuracy) — even if
the diff API is perfectly accurate, the maintainer typo problem remains.

### 4. Release Request Arriving Before Submission Request

If the `IBSEventConsumer` misses the `request.create` event for an SR
(e.g., during reconnection) and an RR for the same incident arrives
before the catch-up fetcher runs, the RR is discarded because no
`SubmissionRequest` with the matching `incident_number` exists in STAMP.

The catch-up fetcher (Pipeline 2, Step 1) will eventually discover the
missed SR, but by that time the RR `request.create` event has already
been discarded. If the RR is still in `new` or `review` state when the
fetcher runs, it will be discovered in the same search query. But if
the RR was accepted before the fetcher runs, it will not appear in the
`states=new,review` search results and will be missed entirely.

**Proposed mitigation**: in the catch-up fetcher, after creating a
missed SR and obtaining its `incident_number` (via acceptance or by
querying `GET /request/{number}`), perform an additional check for RRs
with the same `incident_number` that are not yet in STAMP. This could
use `GET /request?view=collection&project={codestream}&types=maintenance_release`
filtered by the incident project, or a direct query by incident number
if the API supports it.

**Decision pending**: confirm the mitigation approach and verify the
IBS API supports the necessary query patterns.

### 5. Diff API False Positives from Context Lines

The `POST /request/{id}?cmd=diff&withissues=1&view=xml` endpoint has
been verified to return structured `<issues>` entries (see Resolved
Questions). However, it has not been verified whether the issues are
extracted only from **changed lines** or also from **context lines**
surrounding the changes.

If the diff API uses a context window (similar to `diff -C 3` or
`grep -C 3`), CVE-IDs present in unchanged lines near the modified
region could be returned as issues. This would be a structural problem
with the IBS API (not a human error) and would systematically produce
false correlations.

The `state` attribute on each `<issue>` element (`state="added"`,
`state="changed"`) may distinguish genuinely modified issues from
context, but this has not been verified with a test case where known
CVE-IDs exist in unchanged nearby lines.

**Risk**: medium. If false positives occur, they would affect every SR
that modifies a changelog containing pre-existing CVE-IDs — which is
common for packages with a long security history.

**Action**: before implementation, test empirically with an SR that
modifies a changelog file containing pre-existing CVE-IDs (e.g., a
package like `openssl` or `curl` with many historical CVE fixes).
Verify whether the pre-existing CVE-IDs appear in the `<issues>` output
and, if so, whether the `state` attribute distinguishes them from newly
added ones.

**Mitigation if confirmed**: filter issues to only those with
`state="added"` (already done for the source diff endpoint in
`CodestreamReleaseDetector`). If the request diff endpoint does not
populate `state` reliably, fall back to the source diff endpoint
(`POST /source/{project}/{package}?cmd=diff&view=xml&onlyissues=1`)
which is already known to work correctly.
