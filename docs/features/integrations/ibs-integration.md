# OBS / IBS Integration

## Purpose

Integration with Open Build Service instances for package source monitoring
and release detection. Sentinel interacts with two separate OBS instances:

- **IBS** (Internal Build Service, `build.suse.de`): used for SUSE commercial
  products. This is the primary integration for codestream-level release
  detection.
- **OBS** (public, `build.opensuse.org`): used for openSUSE distributions.
  Not currently integrated — see Future Considerations.

## IBS Integration

### Authentication

- IBS API uses HTTP Basic Auth or API tokens
- Credentials are stored as environment variables, never in code
- Configuration:
  - `IBS_API_URL`: IBS API base URL (default: `https://api.suse.de`)
  - `IBS_USERNAME`: IBS API username
  - `IBS_PASSWORD`: IBS API password
  - `IBS_DOWNLOAD_BASE_URL`: HTTP download base URL for repository data
    (default: `https://download.suse.de/ibs`). Used by the
    `ProductReleaseDetector` — see `docs/features/packages/ibs-product-release-detection.md`.

### Key API Operations

The following IBS API endpoints are used by Sentinel for codestream-level
release detection (see `docs/features/packages/ibs-track-release-detection.md`),
product-level release detection (see
`docs/features/packages/ibs-product-release-detection.md`), package
bugowner resolution (see `docs/features/packages/package-bugowner.md`), and
submission request tracking (see `docs/features/packages/ibs-submission-tracking.md`):

#### Project Source Info

```
GET /source/{project}?view=info
```

Returns an XML document with a `<sourceinfo>` element per package in the
project. Each element contains:
- `package` — the source package name
- `srcmd5` — the MD5 checksum of the current source revision

Example response:

```xml
<sourceinfolist>
  <sourceinfo package="containerd" srcmd5="abc123def456..."/>
  <sourceinfo package="podman" srcmd5="789ghi012jkl..."/>
  ...
</sourceinfolist>
```

This endpoint is called once per codestream project and returns all
packages in a single response, making it efficient for change detection
across large projects.

#### Source Diff with Issue Extraction

```
POST /source/{project}/{package}?cmd=diff&view=xml&onlyissues=1&orev={old_md5}&rev={new_md5}
```

Returns an XML document listing references (CVE, Bugzilla) that were added
between the two source revisions. The `onlyissues=1` parameter instructs
IBS to parse the changelog and spec diff internally and return only the
structured issue references.

Example response:

```xml
<sourcediff>
  <issues>
    <issue tracker="cve" name="CVE-2025-1234" state="added"/>
    <issue tracker="bnc" name="1234567" state="added"/>
    <issue tracker="cve" name="CVE-2024-9999" state="changed"/>
  </issues>
</sourcediff>
```

Parameters:
- `orev` — the old source MD5 (baseline revision)
- `rev` — the new source MD5 (current revision)

The `IBSTrackReleaseDetector` filters for issues with `state="added"`
and `tracker` equal to `cve` or `bnc`.

#### Package Bugowner Resolution

Sentinel also uses IBS to resolve package bugowners. These endpoints are
documented in `docs/features/packages/package-bugowner.md` (IBS API Integration
section):

- `GET /search/owner?package={name}&filter=bugowner` — resolve effective
  bugowner through the project hierarchy
- `GET /person/{userid}` — user email and real name
- `GET /group/{group_name}` — group email and member list

#### Request Search

```
GET /request?view=collection&project={project}&states={states}
```

Returns an XML `<collection>` of `<request>` elements matching the given
filters. Used by the `RequestSyncFetcher` to discover open requests and
reconcile state drift. Supports pagination (`limit`, `offset`) and
additional filters:

- `types` — comma-separated action types (e.g.,
  `maintenance_incident,maintenance_release`)
- `states` — comma-separated states (e.g., `new,review,accepted`)
- `package` — filter by target package name
- `created_at_from` — ISO 8601 datetime lower bound

See `docs/features/packages/ibs-submission-tracking.md` for full usage details.

#### Request Detail

```
GET /request/{number}
```

Returns full details of a single request including current state and
action list. Used by the `RequestSyncFetcher` to reconcile requests
that are no longer in `new`/`review` state.

#### Request Diff

```
POST /request/{id}?cmd=diff&withissues=1&view=xml
```

Returns the diff of the request with structured issue references.
Unlike the source diff endpoint, operates directly on the request — no
need for source/target MD5 checksums.

Response includes an `<issues>` block identical in format to the source
diff endpoint:

```xml
<sourcediff>
  <issues>
    <issue tracker="cve" name="CVE-2025-1234" state="added"/>
    <issue tracker="cve" name="CVE-2024-9999" state="changed"/>
  </issues>
</sourcediff>
```

Used by `correlate_submission_request` to extract CVE-IDs from
submission requests. Only issues with `state="added"` and
`tracker="cve"` are processed.

See `docs/features/packages/ibs-submission-tracking.md` for the correlation logic.

### Data Model

IBS-related data is stored in the following tables (see `docs/data-model.md`):

- `CodestreamPackageChecksum`: operational cache storing the last known
  `srcmd5` for each `(codestream_name, package_name)` pair. Shared by the
  `IBSEventConsumer` (real-time) and the `IBSTrackReleaseDetector`
  (periodic catch-up) to detect source changes. See
  `docs/features/integrations/ibs-rabbitmq-integration.md`.
- `TicketPackageTrack.reference`: stores the IBS project name
  (e.g., `SUSE:SLE-15-SP6:Update`) as a string. Tracks are not
  maintained as a separate table.
- `ProductRepository.repo_name`: stores SMELT repository project names
  that map to IBS download URLs. Used by the `ProductReleaseDetector`.

### Service Layer

#### IBSClient (`backend/app/services/ibs_client.py`)

Dedicated client for IBS API communication. Separate from any potential
future `OBSClient` for the public OBS instance, since they would have
independent credentials and may diverge in API behavior.

Methods:
- `get_source_info(project: str) -> dict[str, str]`: calls
  `GET /source/{project}?view=info`, parses the XML response, and returns
  a dictionary mapping package names to their `srcmd5` checksums.
- `get_diff_issues(project: str, package: str, old_md5: str, new_md5: str) -> list[DiffIssue]`:
  calls `POST /source/{project}/{package}?cmd=diff&view=xml&onlyissues=1`,
  parses the XML response, filters for issues with `state="added"` and
  `tracker` equal to `cve` or `bnc`, and returns the filtered list. The
  filtering is performed here in the client so the
  `IBSTrackReleaseDetector` receives pre-filtered results.
- `get_request_diff_issues(request_number: int) -> list[DiffIssue]`: calls
  `POST /request/{id}?cmd=diff&withissues=1&view=xml`, parses the XML
  response, filters for issues with `state="added"` and `tracker="cve"`,
  and returns the filtered list. Used by `correlate_submission_request`.
- `search_requests(project: str, **filters) -> list[RequestInfo]`: calls
  `GET /request?view=collection` with the given filters, parses the XML
  response, and returns structured request data. Used by the
  `RequestSyncFetcher` and `discover_submissions_for_ticket_package`.
- `get_request(number: int) -> RequestInfo`: calls
  `GET /request/{number}`, returns full request details. Used by the
  `RequestSyncFetcher` to reconcile individual request states.

Configuration is injected via the application settings (`IBS_API_URL`,
`IBS_USERNAME`, `IBS_PASSWORD`).

#### IBSTrackReleaseDetector (`backend/app/services/ibs_track_release_detector.py`)

Orchestrates codestream-level release detection using the `IBSClient`.
Full procedure is documented in
`docs/features/packages/ibs-track-release-detection.md`.

### Background Tasks

- `check_ibs_track_releases`: periodic task (every 24 hours at 02:00
  UTC via Celery Beat) that invokes `IBSTrackReleaseDetector.run()`.
  This task is a `BaseFetcher` subclass with `name`, `description`, and
  `default_schedule` attributes. Serves as a catch-up mechanism for
  events missed by the `IBSEventConsumer`. See
  `docs/features/platform/fetcher-infrastructure.md` for the BaseFetcher
  infrastructure and `docs/features/integrations/ibs-rabbitmq-integration.md` for the
  real-time consumer that complements this periodic fetcher.
- `create_ticket_from_detection`: on-demand task enqueued when a CVE fix
  is detected for a CVE with no existing ticket (triggered by either the
  periodic fetcher or the `IBSEventConsumer`). Fetches CVE data from
  NVD, creates the ticket, and resolves packages via SMELT.
- `sync_requests` (`RequestSyncFetcher`): periodic task (every 24 hours
  at 02:30 UTC) that discovers missed submission/release requests and
  reconciles state drift. See `docs/features/packages/ibs-submission-tracking.md`.
- `correlate_submission_request`: on-demand task that calls the IBS
  request diff API, extracts CVE-IDs, and creates join records linking
  submissions to tickets.
- `discover_submissions_for_ticket_package`: on-demand sub-operation task
  enqueued by `add_package_to_ticket` to retroactively discover existing
  SRs/RRs for a newly tracked package.

### Business Rules

1. IBS credentials are validated at startup; warn if not configured
2. IBS API calls use retry logic with exponential backoff
3. All IBS operations are logged for audit purposes
4. The `IBSTrackReleaseDetector` only modifies records with status
   `AFFECTED` or `ANALYSIS`; excluded (soft-deleted) tracks are not modified

## OBS Public Integration

### Status

Not currently integrated. There is no plan to integrate openSUSE package
tracking at this time. This may be evaluated in the future if there is
demand for tracking security updates across openSUSE distributions. If
pursued, it would be addressed in a separate specification.

The public OBS API at `api.opensuse.org` is compatible with IBS but uses
separate authentication. A dedicated `OBSClient`
(`backend/app/services/obs_client.py`) with its own credentials and
configuration (`OBS_API_URL`, `OBS_USERNAME`, `OBS_PASSWORD`) would be
needed. See `docs/data-sources.md` for details on OBS and its RabbitMQ
event bus.

## Security

- IBS and OBS credentials are managed via environment variables
- API calls are made server-side only; credentials are never exposed to
  the frontend
- Operations that modify IBS/OBS state (future: rebuild triggers) require
  the Vulnerability Analyst or Admin role
