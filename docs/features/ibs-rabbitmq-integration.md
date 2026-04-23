# IBS RabbitMQ Integration

## Purpose

Complement the existing polling-based codestream release detection
(`CodestreamReleaseDetector`, documented in
`docs/features/package-tracking.md`) with a real-time event consumer that
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
  `docs/features/package-tracking.md`, section "Product-level Detection").
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

### Consumed Event

Only one event type is consumed:

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

The routing key for binding is `suse.obs.package.commit`. The `suse`
prefix is the IBS scope; the public OBS instance uses `opensuse`.

### Events Evaluated and Rejected

| Event | Reason for rejection |
|---|---|
| `suse.obs.package.build_success` | Not useful for STAMP — release detection needs source-level CVE reference analysis, not build status |
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
   bind it to the `pubsub` exchange with routing key
   `suse.obs.package.commit`
2. **Consume loop**: for each incoming message, execute the processing
   pipeline (see [Processing Pipeline](#processing-pipeline))
3. **Reconnection**: on connection loss, reconnect with exponential backoff
   (initial: 5s, max: 300s). Log each reconnection attempt at WARNING level
4. **Shutdown**: on SIGTERM/SIGINT, close the connection and drain
   in-progress messages gracefully

#### Deployment

The consumer runs as a **dedicated Celery worker process** (or standalone
process) — separate from the periodic task workers. This ensures the
persistent AMQP connection is not disrupted by Celery task execution
mechanics.

Configuration:
- `IBS_RABBITMQ_URL`: broker URL (default: `amqps://suse:suse@rabbit.suse.de`)
- `IBS_RABBITMQ_ENABLED`: boolean to enable/disable the consumer
  (default: `true`)
- `IBS_RABBITMQ_ROUTING_KEY`: routing key filter (default:
  `suse.obs.package.commit`)

### Processing Pipeline

For each `suse.obs.package.commit` event:

1. **Parse payload**: extract `project`, `package`, `srcmd5` from the JSON
   message body

2. **Filter by active codestream**: check if `project` is in the set of
   active codestreams. This set is built from the distinct
   `codestream_name` values of `TicketPackageCodestream` records with
   status `ANALYSIS` or `AFFECTED`. The set is cached in memory and
   refreshed periodically (every 5 minutes) or on cache miss.
   If the project is not in the set → **acknowledge and discard** the
   message. No further processing.

3. **Lookup cached MD5**: query `CodestreamPackageChecksum` for the
   `(codestream_name=project, package_name=package)` pair.
   - If no cached entry exists (first time seeing this package): save the
     `srcmd5` from the event to `CodestreamPackageChecksum` without
     performing a diff. This mirrors the first-run behavior of the
     periodic fetcher.
   - If the cached `srcmd5` matches the event's `srcmd5` → **discard**
     (already processed, either by a previous event or by the periodic
     fetcher).

4. **Request diff from IBS**: call
   `IBSClient.get_diff_issues(project, package, old_md5=cached_srcmd5, new_md5=event_srcmd5)`
   which invokes:
   ```
   POST /source/{project}/{package}?cmd=diff&view=xml&onlyissues=1&orev={old_md5}&rev={new_md5}
   ```

5. **Process CVE references**: for each CVE-ID in the diff response with
   `state="added"` and `tracker="cve"`, apply the same match logic as the
   periodic fetcher:
   - **Case A** — ticket exists, package tracked in the codestream:
     set `TicketPackageCodestream.status` to `RELEASED` via
     `ticket_mutations` (unless protected status)
   - **Case B** — ticket exists, package not tracked: call
     `add_package_to_ticket(ticket_id, package_name)` to resolve
     codestreams/products via SMELT, then set the originating codestream
     to `RELEASED`
   - **Case C** — no ticket exists: enqueue
     `create_ticket_from_detection` task

   See `docs/features/package-tracking.md`, section "Codestream Match
   Outcomes" for the complete specification of each case.

6. **Update MD5 cache**: write the event's `srcmd5` to
   `CodestreamPackageChecksum` for this `(project, package)` pair. This
   update happens **only if the IBS diff request succeeded** (step 4
   returned HTTP 200). If the diff failed, the MD5 is NOT updated, so
   the periodic catch-up fetcher will re-attempt the diff for this
   package on its next run.

7. **Acknowledge message**: acknowledge the RabbitMQ message after
   processing is complete (manual ack mode).

### Filtering: Application-side Only

RabbitMQ topic exchanges allow wildcard filtering on routing key segments,
but the IBS routing key for commit events is `suse.obs.package.commit` —
the project and package names are in the JSON payload, not in the routing
key. Therefore:

- **No broker-level filtering** by project or package is possible
- The consumer receives **all** `package.commit` events from IBS
- Filtering by active codestream (step 2) is performed in the consumer
  process using an in-memory set lookup — this is very fast (sub-microsecond
  per event)

The expected volume is manageable: most commits on IBS are to development
projects (`Devel:*`, `SUSE:Factory`), not to `SUSE:SLE-*:Update`
codestreams. The ratio of relevant to irrelevant events is low.

## Interaction with Periodic Fetcher

### Shared MD5 Cache

The `IBSEventConsumer` and the `CodestreamReleaseDetector` (periodic
fetcher) share the same `CodestreamPackageChecksum` table. This is the
key mechanism that prevents duplicate work:

- When the consumer processes an event and updates the MD5 cache, the
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
| `IBS_RABBITMQ_URL` | string | `amqps://suse:suse@rabbit.suse.de` | AMQP broker URL |
| `IBS_RABBITMQ_ENABLED` | bool | `true` | Enable/disable the RabbitMQ consumer |
| `IBS_RABBITMQ_ROUTING_KEY` | string | `suse.obs.package.commit` | Routing key for binding |
| `IBS_RABBITMQ_RECONNECT_INITIAL` | int | `5` | Initial reconnect delay in seconds |
| `IBS_RABBITMQ_RECONNECT_MAX` | int | `300` | Maximum reconnect delay in seconds |

## Error Handling

| Condition | Behavior |
|---|---|
| RabbitMQ broker unreachable at startup | Log ERROR, retry with exponential backoff |
| Connection lost during consumption | Log WARNING, reconnect with exponential backoff. Events during disconnection are lost (caught by periodic fetcher) |
| Invalid/unparseable message payload | Log WARNING with raw payload, acknowledge and discard |
| IBS diff request fails (HTTP error, timeout) | Log ERROR, do NOT update MD5 cache. The periodic fetcher will retry on its next run |
| SMELT unreachable during Case B/C | Log ERROR, package addition skipped. The MD5 cache IS updated (the IBS diff succeeded), so neither the consumer nor the periodic fetcher will re-attempt automatically. Same behavior as the periodic fetcher — the condition must be surfaced to operators via monitoring. See `docs/features/package-tracking.md` error handling |
| Active codestream set refresh fails | Log WARNING, continue using stale set. Retry refresh on next interval |

## Monitoring and Observability

The `IBSEventConsumer` reports the following metrics (integrated with the
fetcher dashboard where applicable — see
`docs/features/fetcher-dashboard.md`):

- **Connection status**: connected / disconnected / reconnecting
- **Events received**: total count of `package.commit` events received
- **Events filtered**: count discarded by codestream filter (step 2)
- **Events processed**: count that reached diff analysis (step 4)
- **Diffs succeeded**: count of successful IBS diff requests
- **Diffs failed**: count of failed IBS diff requests
- **CVE matches**: count of CVE references matched to active tickets
  (Case A, B, C breakdowns)

These metrics are exposed via the application's logging and can be
collected by monitoring systems.

**Note**: the `IBSEventConsumer` is NOT a `BaseFetcher` subclass. It is
a long-running consumer, not a periodic fetcher with discrete runs. It
does not have a Celery Beat schedule, and the `FetcherRun` model (which
tracks individual runs with start/end timestamps and item counts) does
not fit its continuous execution model. Monitoring is handled through
connection status tracking and event counters instead.

## Known Limitations

### Unmonitored codestreams

If a maintainer commits a CVE fix to a codestream that has no active
tickets (no `TicketPackageCodestream` records in `ANALYSIS` or `AFFECTED`
status across any ticket), the event is discarded by the codestream
filter. This applies equally to the RabbitMQ consumer and the periodic
fetcher — neither monitors codestreams without active tickets.

STAMP does not maintain an independent table of all active codestreams.
Codestream names exist only as strings in `TicketPackageCodestream`
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

### No broker-level filtering

The consumer receives all `suse.obs.package.commit` events from IBS,
not just those for monitored codestreams. Filtering is performed
application-side. While the per-event filtering cost is negligible
(in-memory set lookup), the consumer must maintain a persistent
connection and process the full event stream.

## Security

- RabbitMQ credentials for `rabbit.suse.de` are embedded in the
  connection URL (public credentials: `suse:suse`). These are read-only
  consumer credentials shared across all SUSE-internal consumers
- The consumer only reads from the exchange — it cannot publish messages
  or modify the exchange/queue configuration
- IBS API credentials for diff requests use the same `IBS_USERNAME` /
  `IBS_PASSWORD` environment variables as the periodic fetcher (see
  `docs/features/obs-integration.md`)

## Dependencies

- `docs/features/package-tracking.md`: defines the codestream-level
  detection logic (Case A/B/C), `CodestreamPackageChecksum` cache, and
  `add_package_to_ticket` function used by the consumer
- `docs/features/obs-integration.md`: defines the `IBSClient` service
  used for diff requests
- `docs/features/fetcher-dashboard.md`: defines `BaseFetcher`
  infrastructure (referenced for contrast — the consumer is NOT a
  `BaseFetcher`)
- `docs/data-sources.md`: documents the IBS RabbitMQ event bus
  connection details, exchange configuration, and event topics
