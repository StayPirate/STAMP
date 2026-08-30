# IBS RabbitMQ Integration

## Purpose

Complement the existing polling-based codestream release detection
(`IBSTrackReleaseDetector`, documented in
`docs/features/packages/ibs-track-release-detection.md`) with a real-time event consumer that
listens to IBS commit events via the RabbitMQ message bus at
`rabbit.suse.de`. This reduces codestream-level detection latency from up
to 24 hours (polling interval) to seconds, while maintaining the periodic
fetcher as a catch-up mechanism for events missed during downtime.

### Goals

- Near-real-time codestream-level release detection for active tickets
- Reduced IBS API load (targeted diff per commit instead of full project
  source-info queries)
- No duplicate work: the RabbitMQ consumer and the periodic fetcher share
  the same MD5 cache (`CodestreamPackageChecksum`) so changes processed
  in real-time are not re-processed by the fetcher

### Non-Goals

- Product-level detection: remains polling-based (see
  `docs/features/packages/ibs-product-release-detection.md`).
  The `suse.obs.repo.published` event was evaluated and rejected — its
  payload lacks the package name, triggering full `updateinfo.xml`
  re-download and re-parse.   Measured cost: ~600-800 ms total per repository (~400-470 ms download,
  ~100-220 ms decompression and parsing), dominated by download. With
  ETag/Last-Modified caching on the periodic fetcher, the benefit does
  not justify the complexity.
- Monitoring codestreams without active tickets (see
  [Known Limitations](#known-limitations))

## IBS RabbitMQ Event Bus

### Connection

- **Broker URL**: `amqps://suse:suse@rabbit.suse.de`
- **Exchange**: `pubsub` (type: topic, durable)
- **Exchange declaration**: `passive=True`, `durable=True` — the exchange
  is managed by IBS; consumers cannot create it
- **Queue**: exclusive, auto-delete — declared by the consumer at
  connection time. Exclusive queues are transient: messages published while
  the consumer is disconnected are lost
- **TLS**: the AMQPS connection to `rabbit.suse.de:5671` validates TLS
  against the SUSE Trust Root CA via `SUSE_CA_CERT_PATH` (see
  `networking.md`, TLS Trust Store Configuration)

### Consumed Events

Three event types are consumed:

**`suse.obs.package.commit`** — emitted when a source package is committed
to any IBS project.

| Payload field | Type   | Description                               |
|---------------|--------|-------------------------------------------|
| `project`     | string | IBS project name (e.g., `SUSE:SLE-15-SP6:Update`) |
| `package`     | string | Source package name (e.g., `openssl`)      |
| `rev`         | string | New revision number                        |
| `srcmd5`      | string | MD5 checksum of the new source revision    |
| `files`       | string | Changed files (truncated by IBS at ~800 chars) |
| `user`        | string | User who committed the change              |

**`suse.obs.request.create`** — emitted when a new request is created in
IBS. Used for submission requests (type `maintenance_incident`) and
release requests (type `maintenance_release`).

**`suse.obs.request.state_change`** — emitted when a request transitions
to a conclusive state (accepted, declined, revoked, superseded).
Non-conclusive transitions (e.g., `declined -> new` on reopen) do NOT
emit this event.

Both request events share the same payload structure:

| Payload field | Type   | Description                               |
|---------------|--------|-------------------------------------------|
| `number`      | int    | IBS request number                         |
| `author`      | string | Original request creator                   |
| `actions`     | array  | List of actions (filter by `type` field)   |
| `state`       | string | Current state (for `state_change`: new state) |
| `oldstate`    | string | Previous state (only in `state_change`)    |
| `who`         | string | User who performed the state change        |

For full payload details and processing logic, see
`docs/features/packages/ibs-submission-tracking.md`, sections "Data Sources" and
"Processing Pipelines".

The routing keys for binding are `suse.obs.package.commit`,
`suse.obs.request.create`, and `suse.obs.request.state_change`. The
`suse` prefix is the IBS scope; the public OBS instance uses `opensuse`.

### Events Evaluated and Rejected

| Event | Reason for rejection |
|---|---|
| `suse.obs.package.build_success` | Not useful for Sentinel — release detection needs source-level CVE reference analysis, not build status |
| `suse.obs.package.build_fail` | Same as above |
| `suse.obs.repo.published` | Payload contains `project`, `repo`, `buildid` but no package name. Would trigger expensive `updateinfo.xml` re-download/parse without knowing what changed. Measured cost: ~600-800 ms per repository. Also fires for all update types (recommended, feature), not just security |
| `suse.obs.package.version_change` | Redundant — `package.commit` already provides the information needed to trigger diff analysis |

## Consumer Architecture

### Component: `IBSEventConsumer`

A long-running service that maintains a persistent connection to the IBS
RabbitMQ broker and processes commit events in real-time.

**Location**: `backend/app/services/ibs_event_consumer.py`

#### Lifecycle

1. **Startup**: connect to `rabbit.suse.de`, declare an exclusive queue,
   bind it to the `pubsub` exchange with routing keys
   `suse.obs.package.commit`, `suse.obs.request.create`, and
   `suse.obs.request.state_change`
2. **Consume loop**: for each incoming message, execute the processing
   pipeline (see [Processing Pipeline](#processing-pipeline))
3. **Reconnection**: on connection loss, reconnect with exponential backoff
   (initial: 5s, max: 300s). Log each reconnection attempt at WARNING
   level. The AMQPS SSL context is rebuilt on each reconnection attempt,
   ensuring that a rotated CA certificate is picked up automatically
   without process restart
4. **Shutdown**: on SIGTERM/SIGINT, close the connection and drain
   in-progress messages gracefully

#### Deployment

The consumer runs as a **standalone process** — a dedicated long-running
service with its own entrypoint, separate from Celery workers and Celery
Beat. It is NOT a Celery worker: it does not consume tasks from any
Celery queue.

The consumer imports the **Celery app module**
(`backend/app/celery_app.py`) at startup. This provides:

- Access to the configured Celery application for task enqueueing
  (`create_ticket_from_detection.delay(...)`)
- Automatic timezone validation (UTC check) inherited from the app
  factory
- Automatic lock sentinel validation inherited from the app factory
  (innocuous — the consumer does not use redbeat)
- `FETCHER_REGISTRY` population via `fetcher_discovery` import
  (side-effect of the Celery app module; the consumer does not use the
  registry)

The consumer does **NOT** execute `bootstrap_fetcher_configs()` at
startup. It does not read or write `FetcherConfig` records — that
responsibility belongs to the API server, Celery workers, and Beat.

Configuration:
- `IBS_RABBITMQ_URL`: broker URL (default: `amqps://suse:suse@rabbit.suse.de` — well-known infrastructure defaults, not sensitive credentials)
- `IBS_RABBITMQ_ENABLED`: boolean to enable/disable the consumer
  (default: `true`). See [Process Startup](#process-startup) for the
  process-level behavior when disabled.
- `IBS_RABBITMQ_ROUTING_KEYS`: comma-separated routing keys (default:
  `suse.obs.package.commit,suse.obs.request.create,suse.obs.request.state_change`)

### Process Startup

#### Complete Startup Sequence

```
1. Celery app module imported (backend/app/celery_app.py)
   -> Celery app factory runs
   -> Timezone validation (RuntimeError if CELERY_TIMEZONE != UTC)
   -> Lock sentinel validation (RuntimeError if redbeat lock disabled)
   -> import app.services.fetcher_discovery (populates FETCHER_REGISTRY — unused by consumer)

2. Read IBS_RABBITMQ_ENABLED from configuration
   -> If false: log INFO, exit with code 0 (see "Disabled Mode" below)

3. Infrastructure connectivity check (fail-fast)
   -> PostgreSQL: execute SELECT 1 with 5-second timeout
      - If unreachable: log CRITICAL, exit with code 1
   -> Redis: execute PING with 5-second timeout
      - If unreachable: log CRITICAL, exit with code 1

4. Build monitored codestream set (initial load from PostgreSQL)
   -> Query TicketPackageTrack records with workflow_type = ibs for active
      tickets
   -> Cache result in memory (subsequent refreshes every 5 minutes)
   -> If the query fails (database error): log CRITICAL, exit with code 1
      (no previous set to fall back to — same fail-fast as step 3)
   -> An empty result (no active tickets) is normal — proceed with empty set

5. Connect to RabbitMQ broker
   -> If unreachable: log ERROR, retry with exponential backoff
      (existing reconnection behavior — see Lifecycle above)

6. Declare exclusive queue, bind routing keys

7. Begin consume loop
```

#### Disabled Mode (`IBS_RABBITMQ_ENABLED=false`)

When the `IBS_RABBITMQ_ENABLED` configuration setting is `false`, the
consumer process performs only steps 1-2 of the startup sequence:

1. Import the Celery app module (validates timezone and lock sentinel
   configuration — ensures a misconfigured Celery app is detected even
   when the consumer is disabled)
2. Read the `IBS_RABBITMQ_ENABLED` setting and detect `false`
3. Log at INFO level: `"IBS RabbitMQ consumer disabled
   (IBS_RABBITMQ_ENABLED=false). Exiting."`
4. Exit with code 0

The process does NOT check PostgreSQL or Redis connectivity when
disabled — infrastructure checks are skipped because the consumer will
not operate.

**Orchestrator interaction**: with `restart: on-failure` (Docker Compose)
or the equivalent Kubernetes restart policy, exit code 0 is not treated
as a failure — the container is not restarted. This allows operators to
disable the consumer via environment variable without removing it from
the deployment manifest.

#### Startup Failure: PostgreSQL Unreachable

If PostgreSQL is unreachable during the startup connectivity check
(step 3):

- The consumer logs at CRITICAL level: `"CRITICAL: IBS RabbitMQ consumer
  startup failed — cannot connect to PostgreSQL: {error}. The monitored
  codestream set cannot be built. Consumer will not start."`
- Exit with code 1
- The orchestrator (Docker/Kubernetes) restarts the container according
  to its restart policy. On the next attempt, if PostgreSQL is
  reachable, the consumer proceeds normally.

**Rationale**: without the monitored codestream set, the consumer cannot
determine which events are relevant. Processing all events
indiscriminately would cause unnecessary IBS API calls (diff requests)
for unmonitored codestreams. Failing fast is safer than operating
blindly.

#### Startup Failure: Redis Unreachable

If Redis is unreachable during the startup connectivity check (step 3,
after PostgreSQL succeeded):

- The consumer logs at CRITICAL level: `"CRITICAL: IBS RabbitMQ consumer
  startup failed — cannot connect to Redis: {error}. Cannot enqueue
  tasks or write heartbeat. Consumer will not start."`
- Exit with code 1
- The orchestrator restarts the container. On the next attempt, if Redis
  is reachable, the consumer proceeds normally.

**Rationale**: without Redis, the consumer cannot enqueue Celery tasks
(`create_ticket_from_detection`) or write its heartbeat. It would
consume and acknowledge messages without producing any downstream
effect — silently discarding events. Failing fast is preferable.

**Contrast with runtime Redis unavailability**: the heartbeat section
(Redis Heartbeat above) specifies that runtime Redis failures for
heartbeat writes are non-fatal (log WARNING, continue operating). This
is different from startup: at runtime, the consumer is already
processing events and the inability to write heartbeat is a monitoring
gap, not a functional failure. Task enqueue failures at runtime are
handled per-event (the event's downstream processing fails, but the
consumer itself continues receiving other events). At startup, however,
total Redis unavailability means the consumer cannot perform ANY of its
downstream responsibilities.

#### Startup Failure: Codestream Set Build Fails

If the initial codestream set query (step 4) fails with a database error
after the connectivity check (step 3) succeeded:

- The consumer logs at CRITICAL level: `"CRITICAL: IBS RabbitMQ consumer
  startup failed — cannot build monitored codestream set: {error}.
  Consumer will not start."`
- Exit with code 1
- The orchestrator restarts the container.

**Distinction from empty result**: an empty result set (zero active
tickets) is normal — especially on fresh installations. The consumer
proceeds with an empty set and relies on the periodic 5-minute refresh
to detect newly created tickets.

**Distinction from runtime refresh failure**: during steady-state
operation, a failed refresh logs WARNING and continues with the previous
(stale) set as fallback. At initial startup, no previous set exists —
the consumer cannot determine which events are relevant, so fail-fast
applies.

### Processing Pipeline

The consumer dispatches messages based on routing key. The
`suse.obs.package.commit` pipeline is described below. The
`suse.obs.request.create` and `suse.obs.request.state_change` pipelines
are specified in `docs/features/packages/ibs-submission-tracking.md`.

All event types share the same monitored codestream set (a
`Dict[codestream_name, has_non_final_tracks: bool]` — see step 2 below
for construction). The filtering differs by event type:

- **`package.commit`**: check `project in set AND
  set[project].has_non_final_tracks == true`
- **`request.create` / `request.state_change`**: check only
  `project in set` (no additional filter — SR/RR events are processed
  for all tracked codestreams regardless of track affectedness status)

Membership always requires at least one parent `TicketPackageTrack` with
`workflow_type = ibs` under an active Ticket. A Git track never contributes
its `reference` to this set and is never mutated by an IBS event.

For each `suse.obs.package.commit` event:

1. **Parse payload**: extract `project`, `package`, `srcmd5` from the JSON
   message body

2. **Filter by monitored codestream**: check if `project` is in the
   monitored codestream set **and** `has_non_final_tracks` is `true`.
   The monitored codestream set is a `Dict[codestream_name,
   has_non_final_tracks: bool]` built from the distinct
   `reference` values of `TicketPackageTrack` records with
   `workflow_type = ibs` belonging to active tickets (ticket status in New,
   Analysis, Analyzed). The `has_non_final_tracks` flag is `true` if
   the codestream has at least one track in `ANALYSIS` or `AFFECTED`.
   VA-excluded and lifecycle-non-actionable tracks are included because
   release detection records factual state regardless of operational
   actionability (see Exclusion and Actionability in
   `docs/features/packages/package-model.md`).
   The set is cached in memory and refreshed periodically (every 5
   minutes) or on cache miss. A single DB query builds the entire dict
   atomically.
   If the project is not in the set, or `has_non_final_tracks` is
   `false` → **acknowledge and discard** the message. No further
   processing.

3. **Lookup cached MD5**: query `CodestreamPackageChecksum` for the
   `(codestream_name=project, package_name=package)` pair.
   - If no cached entry exists (first time seeing this package): apply the
     track detector's bounded current-state/first-run behavior. Do not save the
     event `srcmd5` as an unexamined baseline when doing so could make a
     relevant existing fix permanently undiscoverable.
   - If the cached `srcmd5` matches the event's `srcmd5` → **discard**
     (already processed, either by a previous event or by the periodic
     fetcher).

4. **Request diff from IBS**: call
   `IBSClient.get_diff_issues(project, package, old_md5=cached_srcmd5, new_md5=event_srcmd5)`
   which invokes:
   ```
   POST /source/{project}/{package}?cmd=diff&view=xml&onlyissues=1&orev={old_md5}&rev={new_md5}
   ```

5. **Process CVE references**: for each CVE-ID string in the diff
   response with `state="added"` and `tracker="cve"`:

   **Format validation**: validate via `is_valid_cve_id(cve_id)` (from
   `core.identifiers`). If the value does not match, log WARNING ("IBS
   event diff contains malformed CVE reference: {value} in package
   {package_name}, project {project}") and skip this reference. Continue
   with the next reference.

   For valid CVE-IDs, apply the same match logic as the periodic fetcher:
     - **Case A** — ticket exists, package tracked in the codestream:
       set `TicketPackageTrack.status` to `FIXED` via
        `package_service` (only when current status is `AFFECTED` or
        `ANALYSIS`)
     - **Case B** — ticket exists, package not tracked: call
       `add_package_to_ticket(ticket_id, package_name)` to resolve
       codestreams/products via SMELT, then set the originating track's
       status to `FIXED`
   - **Case C** — no ticket exists: enqueue
     `create_ticket_from_detection` task

   See `docs/features/packages/ibs-track-release-detection.md`, section
   "Codestream Match Outcomes" for the complete specification of each case.

6. **Update MD5 cache**: write the event's `srcmd5` to
   `CodestreamPackageChecksum` for this `(project, package)` pair only after
   every required local outcome from the diff has completed or remains
   discoverable by an independent permanent recovery path. A successful IBS
   diff alone is insufficient. Otherwise retain the previous checksum so the
   periodic fetcher re-attempts idempotent processing.

7. **Acknowledge message**: acknowledge successful RabbitMQ processing only
   after local mutation commits and safe checksum advancement complete.
   Retry-versus-immediate-ack behavior for failed events remains owned by this
   RabbitMQ specification. Whatever policy it chooses must not block the
   consumer indefinitely, advance a recovery-blocking checkpoint, or hide the
   failure from operational monitoring.

### Filtering: Application-side Only

RabbitMQ topic exchanges allow wildcard filtering on routing key segments,
but the IBS routing keys encode the event type, not the project or package
name — those are in the JSON payload. Therefore:

- **No broker-level filtering** by project or package is possible
- The consumer receives **all** events matching the bound routing keys
- Filtering by active codestream and tracked package is performed in the
  consumer process using in-memory set lookups — this is very fast
  (sub-microsecond per event)

The expected volume is manageable: most commits on IBS are to development
projects (`Devel:*`, `SUSE:Factory`), not to `SUSE:SLE-*:Update`
codestreams. The ratio of relevant to irrelevant events is low.

## Interaction with Periodic Fetcher

### Shared MD5 Cache

The `IBSEventConsumer` and the `IBSTrackReleaseDetector` (periodic
fetcher) share the same `CodestreamPackageChecksum` table. This is the
key mechanism that prevents duplicate work:

- When the consumer completely processes an event and updates the MD5 cache,
  periodic fetcher will see the updated MD5 on its next run and skip
  that package (no diff needed)
- When the periodic fetcher processes a package that the consumer missed
  (downtime, reconnection gap), it updates the MD5 cache, and any
  subsequent RabbitMQ event for an already-processed revision will be
  discarded at step 3 (MD5 match)

### Schedule Change

With the RabbitMQ consumer handling real-time detection, the periodic
fetcher schedule is reduced from every 8 hours to **every 24 hours at
02:00 UTC**. The fetcher serves as a catch-up mechanism only, covering:

- Events lost during consumer downtime or reconnection (RabbitMQ queues
  are exclusive and transient — messages sent while disconnected are lost)
- Edge cases where event delivery fails silently

### Operational Independence

The two mechanisms are fully independent:

- If the RabbitMQ consumer is down, the periodic fetcher continues to
  operate normally (with 24-hour latency)
- If the periodic fetcher is disabled, the RabbitMQ consumer continues
  to detect releases in real-time (with no catch-up safety net)
- Both can be enabled/disabled independently via configuration

## Configuration

| Variable | Type | Default | Description |
|---|---|---|---|
| `IBS_RABBITMQ_URL` | string | `amqps://suse:suse@rabbit.suse.de` | AMQP broker URL (default credentials are well-known infrastructure defaults, not sensitive) |
| `IBS_RABBITMQ_ENABLED` | bool | `true` | Enable/disable the RabbitMQ consumer |
| `IBS_RABBITMQ_ROUTING_KEYS` | string | `suse.obs.package.commit,suse.obs.request.create,suse.obs.request.state_change` | Comma-separated routing keys for binding |
| `IBS_RABBITMQ_RECONNECT_INITIAL` | int | `5` | Initial reconnect delay in seconds |
| `IBS_RABBITMQ_RECONNECT_MAX` | int | `300` | Maximum reconnect delay in seconds |

## Error Handling

| Condition | Behavior |
|---|---|
| SSL context creation failure (`TLSConfigurationError` from `build_tls_context()`) | Log ERROR with file path and error detail. Terminate process with non-zero exit code. Do NOT enter the reconnection loop — a corrupt CA file is a configuration error, not a transient network condition. Operator must fix the file and restart the process |
| RabbitMQ broker unreachable at startup | Log ERROR, retry with exponential backoff |
| Connection lost during consumption | Log WARNING, reconnect with exponential backoff. Events during disconnection are lost (caught by periodic fetcher) |
| Invalid/unparseable message payload | Log WARNING with routing key and sanitized parsing detail; do not log the raw payload because request and commit messages can contain personal identifiers. Acknowledge and discard |
| IBS diff request fails (HTTP error, timeout) | Log ERROR, do NOT update MD5 cache. The periodic fetcher will retry on its next run |
| Required downstream processing fails after a successful IBS diff, including SMELT unavailability during Case B/C | Log ERROR and do not advance the MD5 unless an independent permanent owner can still discover every omitted outcome. The periodic fetcher then re-attempts idempotent processing |
| Active codestream set refresh fails | Log WARNING, continue using stale set. Retry refresh on next interval |
| PostgreSQL unreachable at startup | Log CRITICAL, exit with code 1. Orchestrator restarts the container. Consumer cannot build the monitored codestream set without PostgreSQL |
| Redis unreachable at startup | Log CRITICAL, exit with code 1. Orchestrator restarts the container. Consumer cannot enqueue tasks or write heartbeat without Redis |
| Initial codestream set query fails (step 4) | Log CRITICAL, exit with code 1. Orchestrator restarts the container. No previous set to fall back to (distinct from runtime refresh failure, which uses stale set) |

## Monitoring and Observability

The `IBSEventConsumer` is NOT a `BaseFetcher` subclass. It is a
long-running consumer, not a periodic fetcher with discrete runs. It
does not have a Celery Beat schedule, and the `FetcherRun` model (which
tracks individual runs with start/end timestamps and item counts) does
not fit its continuous execution model.

Instead, the consumer reports its state via a **Redis heartbeat** and is
surfaced via the `GET /api/v1/ibs-consumer/status` endpoint (see
Operations API Integration below).

### Redis Heartbeat

The consumer writes its current state to a Redis key every **30 seconds**
with a **TTL of 60 seconds**:

- **Key**: `sentinel:ibs_consumer_status`
- **Value**: JSON object with the following fields:

```json
{
  "status": "connected",
  "status_since": "2026-04-20T02:08:00Z",
  "events_received": 12847,
  "events_relevant": 342,
  "events_processed": 338,
  "processing_failed": 4,
  "last_error": null,
  "reconnect_attempts": 0,
  "next_retry_seconds": null
}
```

- **TTL behavior**: if the consumer process dies (crash, OOM kill, etc.),
  it stops writing to Redis. After 60 seconds the key expires and is
  automatically deleted. The API interprets a missing key as consumer
  status `unreachable`.
- **Counter reset**: all counters (`events_received`, `events_relevant`,
  `events_processed`, `processing_failed`) are reset to zero on each new
  connection. They represent activity since the current connection was
  established, not cumulative totals.
- **Reconnection state**: when the consumer is in reconnecting state,
  it continues writing the heartbeat (the process is alive, only the
  broker connection is down). The `reconnect_attempts` and
  `next_retry_seconds` fields are populated.
- **Redis unavailability**: if Redis is unreachable (any `RedisError` —
  including connection failures and OOM rejections) when the consumer
  attempts to write the heartbeat, the consumer logs a WARNING and
  continues operating normally. Event consumption and processing are
  unaffected — the heartbeat is a status reporting mechanism, not a
  prerequisite for operation. The API will report the consumer as
  `unreachable` (missing key) until Redis becomes available and the
  next heartbeat write succeeds.

### Consumer States

| Status | Meaning | Heartbeat |
|---|---|---|
| `connected` | Consumer is connected to RabbitMQ and processing events | Written every 30s |
| `disconnected` | Connection was just lost; set immediately on connection loss, before the first reconnection attempt begins | Written every 30s |
| `reconnecting` | First reconnection attempt has started; consumer is actively retrying with exponential backoff (5s → 10s → ... → 300s, then every 300s indefinitely) | Written every 30s |
| `unreachable` | The API cannot confirm consumer liveness: the key is absent, the API cannot read Redis, or the stored heartbeat is invalid | Key absent, unreadable, or invalid |

### Metrics

The following counters are tracked in the heartbeat (reset on each new
connection):

- **Events received**: total events received from the broker (includes
  `package.commit`, `request.create`, and `request.state_change`)
- **Events relevant**: `package.commit` events that passed the active
  codestream filter (step 2)
- **Events processed**: `package.commit` events where the IBS diff
  completed successfully (step 4-6)
- **Diffs failed**: `package.commit` events where the IBS diff request
  failed
- **Requests processed**: `request.create` and `request.state_change`
  events successfully processed by the submission tracking pipeline

### Operations API Integration

The consumer state is exposed via the `GET /api/v1/ibs-consumer/status`
endpoint, accessible without authentication. This endpoint is owned by
this specification and will be implemented when the IBS RabbitMQ consumer
integration is enabled. See the Endpoint Permission Map in
`docs/features/identity/rbac.md` for the cross-reference.

## Known Limitations

### Unmonitored codestreams

If a maintainer commits a CVE fix to a codestream that has no active
tickets (no `TicketPackageTrack` records belonging to tickets in New,
Analysis, or Analyzed status), the event is
discarded by the codestream filter. This applies equally to the RabbitMQ
consumer and the periodic fetcher — neither monitors codestreams without
active tickets.

Sentinel does not maintain an independent table of all active codestreams.
Codestream names exist only as strings in `TicketPackageTrack`
records, populated when packages are resolved via SMELT. Monitoring all
codestreams would require a new data source (e.g., deriving codestreams
from `ProductRepository` names), which is not pursued at this time
because the scenario is rare in practice: if a codestream has active
tickets, it is monitored; if it has no active tickets, all its packages
are in a final status.

### Transient queues

RabbitMQ exclusive queues are transient — messages published while the
consumer is disconnected are permanently lost. The periodic catch-up
fetcher (every 24 hours at 02:00 UTC) mitigates this, but events lost
during downtime may not be detected for up to 24 hours.

Disabling the corresponding periodic fetcher is an explicit operational choice
that removes this recovery guarantee. The RabbitMQ consumer continues to
operate, but missed or permanently failed events then have no polling safety
net.

### No broker-level filtering

The consumer receives all `suse.obs.package.commit` events from IBS,
not just those for monitored codestreams. Filtering is performed
application-side. While the per-event filtering cost is negligible
(in-memory set lookup), the consumer must maintain a persistent
connection and process the full event stream.

## Open Points

- **Per-message correlation ID.** Define a per-message correlation ID
  for the consumer (e.g., `ibs_event_id`), bound at the start of
  processing each AMQP message and reset at the end, so that log lines
  produced while handling a given `package.commit` / `request.create`
  / `request.state_change` event are correlatable to that specific
  event. See `docs/features/platform/logging.md` (Correlation IDs) for
  the general per-execution-unit correlation model this would follow.

- **Heartbeat during initial RabbitMQ connection (step 5).** If
  RabbitMQ is unreachable at first startup (step 5 retries with
  exponential backoff), it is unspecified when the heartbeat loop
  begins. If the heartbeat starts only after the first successful
  connection, a consumer stuck in step 5 for hours is indistinguishable
  from a crashed process via the `/api/v1/ibs-consumer/status` endpoint.
  Decide whether: (a) the heartbeat loop starts immediately after step 4
  (reporting `reconnecting` state while retrying), or (b) it starts only
  after the first successful connection, accepting the observability gap.

- **Per-event database query failure during steady-state.** The Error
  Handling table specifies behavior for IBS diff failures and SMELT
  unavailability, but does not cover the case where a per-event
  database query (MD5 lookup at processing pipeline step 3, or MD5
  update at step 6) fails due to PostgreSQL unavailability. Decide
  whether the message is: (a) acknowledged and discarded (event lost),
  (b) NACKed/requeued (risks infinite retry loop if PG is down), or
  (c) acknowledged with the event skipped and logged for operator
  alerting. The chosen policy must satisfy the shared non-blocking,
  checkpoint-safety, and observability boundary above; this foundation does
  not require per-message retries.

- **Exchange declaration failure at step 6.** If the consumer connects
  to RabbitMQ successfully (step 5) but the `pubsub` exchange does not
  exist when declaring the queue with `passive=True` (step 6), the
  broker returns a channel error (404 NOT_FOUND). This is not a
  transient network issue — retrying will not help. Decide whether this
  is treated as: (a) a fatal configuration error (exit with code 1), or
  (b) a transient error that enters the reconnection loop (in case IBS
  ops are performing maintenance and the exchange will reappear).

## Security

- RabbitMQ credentials for `rabbit.suse.de` are embedded in the
  connection URL (public credentials: `suse:suse`). These are read-only
  consumer credentials shared across all SUSE-internal consumers
- The consumer only reads from the exchange — it cannot publish messages
  or modify the exchange/queue configuration
- IBS API credentials for diff requests use the same `IBS_USERNAME` /
  `IBS_PASSWORD` environment variables as the periodic fetcher (see
  `docs/features/integrations/ibs-integration.md`)

## Dependencies

- `docs/features/packages/package-model.md`: defines the codestream-level
  detection logic (Case A/B/C), `CodestreamPackageChecksum` cache, and
  `add_package_to_ticket` function used by the consumer
- `docs/features/integrations/ibs-integration.md`: defines the `IBSClient` service
   used for diff requests
- `docs/features/platform/fetcher-infrastructure.md`: defines `BaseFetcher`
  infrastructure (referenced for contrast — the consumer is NOT a
  `BaseFetcher`)
- `docs/data-sources.md`: documents the IBS RabbitMQ event bus
  connection details, exchange configuration, and event topics
