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

### Workflow boundary

Sentinel invokes IBS package, source, diff, request, and repository operations
only for package-tree occurrences whose persisted parent
`TicketPackageTrack.workflow_type` is `ibs`. `TicketPackageTrack.reference` is
not sufficient evidence: it may instead identify a Git branch. An IBS consumer
MUST filter by the workflow discriminator before passing the reference as an
IBS project. Git tracks receive no IBS source checksum, diff, request
correlation, RabbitMQ-driven mutation, or IBS release mutation.

Product-level IBS repository processing qualifies each
`TicketPackageProduct` through its parent IBS track. The same catalog Product
below a Git track is outside IBS scope. Package maintainership is not an IBS
consumer: Sentinel obtains it exclusively from SMELT for both IBS and Git/SLFO
package occurrences.

The complete active-ticket, reactivation, acceleration, and recovery ownership
contract is defined in `docs/features/packages/package-model.md` (IBS Workflow
Applicability and Convergence).

### Origins and Authentication

- The IBS API at `IBS_API_URL` uses HTTP Basic Auth or API tokens.
  `IBS_USERNAME` and `IBS_PASSWORD` are stored as environment variables, never
  in code. Empty or unset credentials do not block application startup;
  credentialed IBS API consumers fail at runtime.
- `IBS_DOWNLOAD_BASE_URL` is a separate anonymous HTTPS Product-repository
  front door (default: `https://download.suse.de/ibs`). Product release
  detection does not send IBS API credentials, an `Authorization` header, or
  another credential to the download front door or its permitted redirect
  target. Its complete validation, path, and redirect contract is defined in
  `docs/features/packages/ibs-product-release-detection.md`.

### Key API Operations

The following credentialed IBS API endpoints are used by Sentinel for
codestream-level release detection (see
`docs/features/packages/ibs-track-release-detection.md`) and submission request
tracking (see `docs/features/packages/ibs-submission-tracking.md`). Product-level
release detection uses the separate anonymous repository-download boundary
below, not these API endpoints.

#### Project Source Info

```
GET /source/{project}?view=info&nofilename=1&package={package}...
```

Returns an XML document with one `<sourceinfo>` element for each selected
package. The `package` query parameter is repeatable. Track release detection
requests only logical package names already represented by eligible Sentinel
tracks; it does not enumerate an entire project. `nofilename=1` avoids recipe
and filename processing that is unrelated to release observation.

Consumed fields:

- `package` — requested logical source package name;
- `srcmd5` — checksum of the current **expanded** source tree;
- optional `lsrcmd5` — local link revision, used only as link context; and
- optional `linked` children — source packages contributing to the expanded
  state, used only as link context.

`verifymd5`, `lsrcmd5`, and revision numbers are not persisted release
checkpoints. For a linked package, `srcmd5` can change when a target changes
even if `lsrcmd5` remains unchanged.

Example response:

```xml
<sourceinfolist>
  <sourceinfo package="fictional-package"
              srcmd5="0123456789abcdef0123456789abcdef"
              lsrcmd5="fedcba9876543210fedcba9876543210">
    <linked project="SUSE:SLE-15-SP6:Update"
            package="fictional-package.snapshot"/>
  </sourceinfo>
</sourceinfolist>
```

The response must be well-formed and completely consumed. Each requested
package must appear exactly once with a 32-character hexadecimal `srcmd5` and
without an upstream `error` child. A missing requested entry, duplicate entry,
malformed required field, or error-bearing entry makes that package unusable;
dependent tracks fail without advancing their checkpoints. Valid sibling
packages may continue when the parser can isolate the invalid entry. Malformed
or interrupted XML invalidates the complete request. Unrequested entries are
ignored and never create detector scope.

#### Source Diff with Issue Extraction

```
POST /source/{project}/{package}?cmd=diff&view=xml&onlyissues=1&expand=1&orev={old_md5_or_0}&rev={new_md5}
```

Returns an XML document listing structured issue-reference changes between
two expanded source states. `onlyissues=1` asks IBS to extract issue references
from the source changes. `expand=1` is required so linked packages are compared
using their expanded source trees. `orev=0` represents an empty source tree and
is used for a track's first observation and unavailable-history fallback.

Example response:

```xml
<sourcediff>
  <issues>
    <issue tracker="cve" name="2025-1234" label="CVE-2025-1234" state="added"/>
    <issue tracker="bnc" name="1234567" state="added"/>
    <issue tracker="cve" name="2024-9999" label="CVE-2024-9999" state="changed"/>
  </issues>
</sourcediff>
```

Parameters:
- `orev` — the previous expanded `srcmd5`, or `0` for an empty source tree
- `rev` — the current expanded `srcmd5`

For track release detection, only `tracker="cve"` with `state="added"` or
`state="changed"` enters CVE matching. `label` is the canonical CVE identity;
`name` is tracker-native and is not used as a CVE ID. `state="deleted"`, `bnc`,
and every other tracker are ignored. A malformed canonical CVE label is logged
with bounded sanitized context and ignored; it is never interpreted as a CVE.

The complete XML document must parse successfully before dependent local
outcomes are committed. A malformed or interrupted response fails every track
depending on that diff. A valid empty issue set is a successful no-match.

IBS can return 400 or 404 when a historical revision is unavailable. Before
implementation, a representative live response must establish the exact
status/body discriminator that distinguishes unavailable history from malformed
input, authorization failure, or another client error. Only a positively
identified unavailable-history response permits the detector's `orev=0`
fallback; an ambiguous 4xx is an ordinary failure.

Both source-info and source-diff XML are processed incrementally with DTDs,
external entities, and parser network access disabled. Sentinel does not impose
an arbitrary response-byte or issue-count cap, but never accepts a truncated or
partially parsed document. The shared request and fetcher-run timeouts remain
the execution bounds.

#### Track-Release Contract Verification Status

Sanitized read-only verification against IBS on 2026-09-03 established:

- source-info supports repeatable package selection and
  `view=info&nofilename=1`;
- `srcmd5` is the expanded source state, while linked packages may also expose
  a distinct local `lsrcmd5` and `linked` elements;
- linked-package source diffs require `expand=1` to expose the relevant
  structured issues;
- `orev=0` is accepted as an empty source tree;
- source diff entries expose `tracker`, `state`, tracker-native `name`, and
  canonical `label`, including empty, mixed-tracker, multi-CVE, `added`,
  `changed`, and `deleted` results; and
- unavailable historical references can produce 400 or 404, depending on the
  reference form.

OBS source inspection established that `added`, `changed`, and `deleted`
describe changes to issue references extracted from package changes files. The
exact sanitized IBS error discriminator for an unavailable historical checksum
is still unverified and remains the implementation gate stated above.

#### Request Search

```
GET /request?view=collection&project={project}&states={states}
```

Returns an XML `<collection>` of `<request>` elements matching the given
filters. Used by the `SyncIbsRequests` to discover open requests and
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
action list. Used by the `SyncIbsRequests` to reconcile requests
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

- `TrackReleaseCheckpoint`: operational state storing the last expanded
  `srcmd5` successfully examined for one `TicketPackageTrack`. At most one row
  exists per track. Polling, catch-up, and package-commit acceleration share
  this state without using it as Ticket audit history. See
  `docs/features/integrations/ibs-rabbitmq-integration.md`.
- `TicketPackageTrack.reference`: stores the IBS project name
  (e.g., `SUSE:SLE-15-SP6:Update`) as a string. Tracks are not
  maintained as a separate table.
- `ProductRepository.repo_name`: stores SMELT repository project names
  that map to anonymous IBS Product repository URLs. Used by
  `detect_ibs_product_releases`.

### Service Layer

#### IBSClient (`backend/app/services/ibs_client.py`)

Dedicated client for IBS API communication. Separate from any potential
future `OBSClient` for the public OBS instance, since they would have
independent credentials and may diverge in API behavior.

Methods:
- `get_source_info(project: str, packages: Collection[str]) -> Mapping[str,
  SourceInfoResult]`: calls targeted
  `GET /source/{project}?view=info&nofilename=1` with one repeatable `package`
  parameter per requested logical package. It returns one validated expanded
  `srcmd5` result or one explicit validation failure per requested package;
  malformed document-level XML raises an IBS response-data error for the whole
  call. An empty package collection is rejected without an HTTP request.
- `get_diff_issues(project: str, package: str, old_md5: str,
  new_md5: str) -> list[DiffIssue]`: calls the source diff endpoint with
  `expand=1` (`old_md5` is either a 32-character expanded checksum or the
  literal string `"0"`), parses the complete XML response, and returns structured issue
  fields sufficient for the detector to evaluate `tracker`, `state`, and
  `label`. It does not reinterpret `bnc` names as CVE IDs. A positively
  identified unavailable `old_md5` raises a distinct internal condition used
  by the detector's fallback; every other HTTP or response-data failure remains
  distinct from successful empty results.
- `get_request_diff_issues(request_number: int) -> list[DiffIssue]`: calls
  `POST /request/{id}?cmd=diff&withissues=1&view=xml`, parses the XML
  response, filters for issues with `state="added"` and `tracker="cve"`,
  and returns the filtered list. Used by `correlate_submission_request`.
- `search_requests(project: str, **filters) -> list[RequestInfo]`: calls
  `GET /request?view=collection` with the given filters, parses the XML
  response, and returns structured request data. Used by the
  `SyncIbsRequests` and `discover_submissions_for_ticket_package`.
- `get_request(number: int) -> RequestInfo`: calls
  `GET /request/{number}`, returns full request details. Used by the
  `SyncIbsRequests` to reconcile individual request states.

For the two source-release methods, `SourceInfoResult` and `DiffIssue` are typed
internal values rather than raw XML elements. A `SourceInfoResult` contains
either one validated `SourceInfo` or one bounded package-validation reason,
never both. The methods have these escaping outcomes:

| Condition | Signal to caller |
|---|---|
| Empty source-info package collection | `ValueError` before HTTP I/O |
| Transport failure, timeout, rate limit, or non-history HTTP error after shared retry handling | The corresponding `httpx` exception propagates with the response body excluded from application logs |
| Malformed/interrupted XML or document-level schema failure | `IBSResponseDataError` |
| One requested source-info package missing, duplicated, error-bearing, or carrying an invalid `srcmd5` | A per-package failed result in the returned mapping; valid sibling package results remain usable |
| Source diff old revision positively identified as unavailable | `IBSHistoricalRevisionUnavailable` |
| Source diff contains an issue with an unusable structural shape | `IBSResponseDataError` |
| Source diff is valid and contains no qualifying issue | Empty list |

`IBSResponseDataError` and `IBSHistoricalRevisionUnavailable` are internal
integration exceptions caught by the detector; they never reach an API
handler. `IBSHistoricalRevisionUnavailable` is emitted only after the live
status/body discriminator required above has been verified. Until then, an
ambiguous 400/404 remains the original HTTP error.

The methods are deterministic for one response and have no local or upstream
mutation side effects. Re-invocation may observe a newer IBS source state.

Configuration is injected via the application settings (`IBS_API_URL`,
`IBS_USERNAME`, `IBS_PASSWORD`).

**HTTP client infrastructure**: `IBSClient` uses the standalone HTTP
client factory (`backend/app/services/http_client.py`) directly, with
the following overrides:

- `Accept: application/xml` (IBS API returns XML, not JSON)
- TLS validated against the SUSE Trust Root CA via the combined trust
  store (see `networking.md`, TLS Trust Store
  Configuration)
- Transport-level retry active (4 attempts for 5xx/timeout/connection
  errors)
- Long-lived client: instantiated per-process, not per-request

#### Product Repository Download Boundary

`detect_ibs_product_releases` uses the shared `BaseFetcher` HTTP client for
anonymous GET requests rooted at `IBS_DOWNLOAD_BASE_URL`; it does not use
`IBSClient`. It requests and validates:

- `repodata/repomd.xml`: exact repomd namespace, one optional updateinfo data
  entry, artifact location, compressed/open checksums and sizes, and metadata
  timestamp; and
- the selected `updateinfo.xml.gz`: one bounded gzip member containing the
  complete no-namespace `updates` document, stable security advisory fields,
  exact CVE references, issued Unix seconds, and validated `src`/`nosrc` package
  entries.

The download boundary exposes successful non-match, validated snapshot, and
bounded failure outcomes to the detector; it does not mutate local state. HTTP
404 for `repomd.xml` and valid metadata without updateinfo are successful
non-matches. Transport/HTTP, path/redirect, checksum/size, decompression,
snapshot-consistency, and XML/advisory failures remain distinguishable bounded
failure categories. The owning Product detector specification defines every
field, resource limit, path constraint, redirect rule, retry effect, and
mutation consequence; this integration overview does not redefine them.

**Retry safety for POST operations**: IBSClient's POST operations
(`cmd=diff` for source diff and request diff) are semantically
read-only — they compute diffs without modifying IBS server state.
Therefore, IBSClient enables `retry_non_idempotent=True` for these
calls, allowing automatic retry on timeout and connection error as
for idempotent methods. See `docs/features/platform/networking.md`
(Transport-Level Retry, Method Safety) for the opt-in mechanism.

#### Track Release Reconciliation

`reconcile_ibs_track_releases()` reconciles supplied existing IBS track IDs by obtaining authoritative current
source info, comparing each track's own checkpoint, and applying atomic local
outcomes. The periodic fetcher, per-Ticket catch-up, and RabbitMQ package-commit
path use this same boundary. Full behavior is documented in
`docs/features/packages/ibs-track-release-detection.md`.

### Background Tasks

- `detect_ibs_track_releases`: periodic task (every 24 hours at 02:00
  UTC via Celery Beat) that invokes the `DetectIbsTrackReleases` fetcher via
  the inherited `BaseFetcher.run()` lifecycle. Its `execute()` method delegates
  to `reconcile_ibs_track_releases()`. The fetcher is a `BaseFetcher` subclass with `name`, `description`, and
  `default_schedule` attributes. Serves as a catch-up mechanism for
  events missed by the `IBSEventConsumer`. See
  `docs/features/platform/fetcher-infrastructure.md` for the BaseFetcher
  infrastructure and `docs/features/integrations/ibs-rabbitmq-integration.md` for the
  real-time consumer that complements this periodic fetcher.
- `sync_ibs_requests` (`SyncIbsRequests`): periodic task (every 24 hours
  at 02:30 UTC) that discovers missed submission/release requests and
  reconciles state drift. See `docs/features/packages/ibs-submission-tracking.md`.
- `correlate_submission_request`: on-demand task that calls the IBS
  request diff API, extracts CVE-IDs, and creates join records linking
  submissions to tickets.
- `discover_submissions_for_ticket_package`: on-demand sub-operation task
  enqueued by `add_package_to_ticket` to retroactively discover existing
  SRs/RRs for a newly tracked package.
- `detect_ibs_product_releases`: periodic `BaseFetcher` task (daily at 04:00
  UTC) that reconciles unreleased Product occurrences through the anonymous
  Product repository download boundary. See
  `docs/features/packages/ibs-product-release-detection.md`.

### Business Rules

1. Credentialed IBS API consumers warn when IBS credentials are empty or unset;
   anonymous Product repository downloads never consume those credentials
2. IBS mutations use their owning service and audit contracts; read-only
   external operations use bounded operational logging rather than audit events
3. Track release reconciliation only modifies records with status
   `AFFECTED` or `ANALYSIS` (soft-deleted tracks in these statuses are
   still modified — see `docs/features/packages/package-service.md`,
   Package-tree exclusion and actionability)
4. Every IBS consumer selects `TicketPackageTrack.workflow_type = ibs`; a Git
   reference is never interpreted as an IBS project
5. Ordinary IBS polling covers active Tickets only. Ticket reactivation first
   reconciles its package tree, then runs targeted source-specific catch-up
6. Track release detection reconciles only existing represented tracks. It
   never discovers CVEs or creates Tickets, packages, tracks, or Products

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
- Product repository downloads are anonymous and never attach or forward IBS
  API credentials
- Operations that modify IBS/OBS state (future: rebuild triggers) require
  the Vulnerability Analyst or Admin role
- XML parsers disable DTDs, external entities, and parser network access;
  authenticated response bodies are never written to logs
