# Package Bugowner Tracking

## Purpose

Track the current bugowner (package maintainer) for each source package
referenced in Sentinel tickets. Bugowner is global package metadata: one
current person or group applies to the source package regardless of whether a
particular occurrence is maintained through an IBS or Git track.

This feature enables:

1. Identifying who is responsible for preparing and submitting fixes for
   each package in a ticket
2. Filtering tickets by bugowner (e.g., "show me all active tickets for
   packages I maintain")
3. Future integration with a maintainer dashboard where each maintainer
   can see pending submissions and track progress (separate spec)

This specification is the authoritative source for bugowner resolution,
caching, and maintenance. Its existing resolver uses IBS; source authority,
fallback behavior, conflicts between sources, and the resulting fetcher name
must be finalized in this bugowner specification before
implementation. Callers use a source-neutral ensure-bugowner operation and do
not select IBS or Git. See `docs/features/packages/package-model.md` for the
shared workflow and convergence boundary, and
`docs/features/integrations/ibs-integration.md` for the current IBS API details.

## Domain Concepts

### Bugowner

In IBS (and OBS), the **bugowner** is a role assigned to a person or
group at the package level. It identifies who is responsible for handling
bug reports and maintenance of that package. This is the same concept
referred to as "bugowner" in Bugzilla when filing bugs against SUSE
packages.

The bugowner is resolved through the IBS project hierarchy — a package
inherits the bugowner from the most specific project that defines it.
For example, `curl` in `SUSE:SLE-15-SP6:Update` may inherit its
bugowner from `SUSE:ALP:Source:Standard:1.0` where the role is actually
defined.

### Bugowner Types

A bugowner can be either a **person** or a **group**:

| Type   | Description                                   | Example                  |
|--------|-----------------------------------------------|--------------------------|
| person | Individual IBS user with a `userid` and email | `jdoe` (`john.doe@suse.com`) |
| group  | Team with a collective email and member list   | `kernel-team` (`kernel-team@suse.de`) |

When the bugowner is a group, Sentinel stores both the group-level
information (name and collective email) and the individual members of
the group. This enables the future maintainer dashboard to show each
group member the tickets relevant to their group.

### Bugowner Stability

The bugowner of a package changes very rarely — typically only when a
maintainer leaves the organization and a replacement is designated. For
practical purposes, the bugowner can be considered stable over the
lifetime of most tickets.

However, because changes do happen, Sentinel uses a shared cache with two
update mechanisms:

1. **On-demand availability**: when package addition creates a new IBS track,
   Sentinel asks the bugowner feature to ensure a value is available. A usable
   shared cache entry satisfies the request without external I/O; otherwise
   the resolver applies the source strategy owned by this specification
2. **Periodic maintenance**: a background fetcher runs every 14 days
   to verify and repair the cache (see [Maintenance Fetcher](#maintenance-fetcher))

Because the cache is shared across all tickets, when a bugowner changes
and the cache is updated, all active tickets referencing that package
immediately reflect the new bugowner. This prevents tickets from being
"orphaned" when a maintainer leaves.

## Data Model

See `docs/data-model.md` for the full schema. The tables defined by this
feature are:

### PackageBugowner

Caches the current bugowner for each source package actively tracked in
Sentinel tickets. This is a shared cache — all tickets and workflows
referencing the same package point to the same bugowner record.

Records are created on demand when a new IBS track first requires a value and
no usable shared value exists. The current periodic owner is the
`sync_ibs_bugowners` fetcher; its final source strategy and name are deferred as
described above.
Records are removed when the package no longer appears in any active
ticket.

See `docs/data-model.md` for the full column listing.

### PackageBugownerMember

Stores the individual members of group bugowners. Populated only when
`PackageBugowner.bugowner_type` is `group`. Each member's IBS userid
and email are stored to support the future maintainer dashboard, where
each group member can see tickets for packages maintained by their group.

Records are managed as a set: when the group membership changes (detected
by the maintenance fetcher), new members are added and departed members
are removed.

See `docs/data-model.md` for the full column listing.

## IBS API Integration

Sentinel uses three IBS API endpoints to resolve bugowner information. All
endpoints use the same authentication as existing IBS integrations (see
`docs/features/integrations/ibs-integration.md`).

### Owner Search

```
GET /search/owner?package={package_name}&filter=bugowner
```

Resolves the effective bugowner of a package through the IBS project
hierarchy. The `filter=bugowner` parameter MUST always be included to
restrict results to the `bugowner` role — without it, the endpoint may
return project-level `maintainer` roles which are not relevant for
package ownership.

Returns an XML response:

```xml
<collection>
  <owner rootproject="SUSE" project="SUSE:ALP:Source:Standard:1.0" package="curl">
    <group name="pkg-maintainers" role="bugowner"/>
  </owner>
</collection>
```

Or for a person:

```xml
<collection>
  <owner rootproject="SUSE" project="SUSE:SLE-15:GA" package="apache2">
    <person name="jdoe" role="bugowner"/>
  </owner>
</collection>
```

If the package does not exist or has no bugowner, the response is an
empty collection:

```xml
<collection/>
```

### Person Details

```
GET /person/{userid}
```

Returns the email and real name of an IBS user:

```xml
<person>
  <login>jdoe</login>
  <email>john.doe@suse.com</email>
  <realname>John Doe</realname>
  <state>confirmed</state>
</person>
```

### Group Details

```
GET /group/{group_name}
```

Returns the group email and full member list:

```xml
<group>
  <title>pkg-maintainers</title>
  <email>pkg-maintainers@suse.de</email>
  <maintainer userid="asmith"/>
  <maintainer userid="bwilson"/>
  <maintainer userid="cjones"/>
  <person>
    <person userid="asmith"/>
    <person userid="bwilson"/>
    <person userid="cjones"/>
  </person>
</group>
```

The `<person>` block lists all group members. The `<maintainer>` entries
are group administrators and are a subset of the members — Sentinel does
not distinguish between group administrators and regular members.

## Bugowner Resolution Algorithm

The source-neutral caller invokes the conceptual operation
`ensure_bugowner(package_name: str)`. If a usable `PackageBugowner` record
already exists, return it without external I/O or mutation. Otherwise the
resolver derives eligible external sources from the package's persisted track
occurrences; the caller does not pass a workflow or source selector. In
particular, IBS may be queried only when at least one relevant persisted track
has `workflow_type = ibs`. A Git-only package is never sent to IBS. Under the
current IBS strategy, an eligible lookup proceeds as follows:

1. Query IBS: `GET /search/owner?package={package_name}&filter=bugowner`
2. If the response is an empty `<collection/>`, set `bugowner_type`,
   `bugowner_name`, and `bugowner_email` to `NULL` in the cache and
   stop
3. Extract the bugowner type (`person` or `group`) and name from the
   response
4. **Email normalization**: all email addresses obtained from IBS API
   responses (`/person/{userid}` and `/group/{group_name}`) MUST be
   normalized to lowercase before storage (`value.lower()`). This
   applies to `bugowner_email` in `PackageBugowner` records (person and
   group bugowners) and `email` in `PackageBugownerMember` records
   (group members). This guarantees case-insensitive matching with
    `User.email` (also stored as lowercase) without
   requiring runtime `ILIKE` or `lower()` in queries.
5. If the type is `person`:
   a. Query `GET /person/{userid}` to obtain the email
   b. Create or update the `PackageBugowner` record with
      `bugowner_type = 'person'`, `bugowner_name = {userid}`,
      `bugowner_email = {email}`
   c. If any `PackageBugownerMember` records exist for this
      `PackageBugowner` (from a previous group assignment), delete them
6. If the type is `group`:
   a. Query `GET /group/{group_name}` to obtain the group email and
      member list
   b. Create or update the `PackageBugowner` record with
      `bugowner_type = 'group'`, `bugowner_name = {group_name}`,
      `bugowner_email = {group_email}`
   c. For each member `userid` in the `<person>` block, query
      `GET /person/{userid}` to obtain the email
   d. Synchronize `PackageBugownerMember` records: add new members,
      remove members no longer in the group, update emails if changed

The usable-record short circuit belongs only to the on-demand
`ensure_bugowner()` operation and reactivation catch-up. Periodic maintenance
Operation 2 deliberately bypasses that short circuit and executes the eligible
lookup steps above to refresh an existing record. Operation 3 uses the same
lookup steps to create a missing record.

### IBS Query Failure Handling

If any IBS API call fails during bugowner resolution:

- During **on-demand population** (package added to ticket): log the
  error at `WARNING` level and continue. The `PackageBugowner` record
  is not created — the maintenance fetcher will pick it up in the next
  cycle. Package addition to the ticket MUST NOT fail due to a bugowner
  resolution failure.
- During **maintenance fetcher** execution: log the error, call
  `self.record_failed()`, and continue to the next package. The stale
  data is preserved until the next successful update.

## Maintenance Fetcher

The `sync_ibs_bugowners` fetcher is a `BaseFetcher` subclass that
performs periodic maintenance of the bugowner cache. It runs every
14 days and executes three operations in sequence:

### Fetcher Properties

| Property | Value |
|----------|-------|
| Fetcher name | `sync_ibs_bugowners` |
| Class name | `SyncIbsBugowners` |
| Schedule | Every 14 days at 03:00 UTC (`0 3 */14 * *`) |
| Source | IBS (`build.suse.de`) |
| Scope | All `PackageBugowner` records + packages with at least one IBS track under an active Ticket that are missing from the cache |
| Auth | HTTP Basic / API token (internal) |
| `participates_in_catch_up` | `True` — participates in per-ticket catch-up on ticket reactivation |
| Custom settings | No |

#### Catch-Up

`SyncIbsBugowners` implements `catch_up()` as a custom override. See
[fetcher-infrastructure.md](../platform/fetcher-infrastructure.md)
("Per-Ticket Catch-Up: `catch_up()` Method") for the base class
contract.

**Scope**: after package-tree re-resolution has committed, extracts the
Ticket's package names. A usable shared record is a no-op; otherwise the
catch-up invokes the source-neutral resolver. The current implementation source
is IBS, but catch-up callers do not encode that choice. For a package with no
IBS track, the current resolver performs no IBS request.

This specification must define cache usability, source selection, fallback,
and complete per-item failure behavior before implementation. This foundation
fixes only the source-neutral caller boundary and global per-package result.

### Operation 1: Cleanup

Remove `PackageBugowner` records for packages that no longer appear in
any active ticket.

1. Query all distinct `package_name` values from `PackageBugowner`
2. For each `package_name`, check if there exists at least one
   `TicketPackage` record where:
   - `TicketPackage.package_name` matches, AND
   - `TicketPackage.deleted_at IS NULL` (not soft-deleted), AND
   - The parent `Ticket` is **active** (status in `New`, `Analysis`,
       `Analyzed`)
3. If no active ticket references the package, delete the
   `PackageBugowner` record and its associated `PackageBugownerMember`
   records
4. Call `self.record_updated()` for each removed record

Lifecycle actionability does not participate in this cache retention test. A
package with only EOL Products remains associated while a non-deleted
`TicketPackage` under an active Ticket references it. A directly soft-deleted
package does not retain the cache record.

### Operation 2: Update

Refresh bugowner data from IBS only for remaining `PackageBugowner` records
whose package has at least one IBS track under an active Ticket. A global cache
record referenced only by Git tracks is retained by Operation 1 but is not sent
to the IBS resolver.

1. For each `PackageBugowner` record not removed in Operation 1 whose package
   has at least one active-Ticket track with `workflow_type = ibs`:
   a. Capture the existing type, name, email, and group-member set, then
      execute the eligible lookup steps in the
      [Bugowner Resolution Algorithm](#bugowner-resolution-algorithm) for the
      `package_name`, bypassing the on-demand usable-record short circuit
   b. The lookup updates the bugowner record and, for a group, synchronizes the
      member list: add new members, remove departed members, and update changed
      emails
   c. Compare the resulting record and member set with the captured values. If
      the type, name, email, or member set changed, call
      `self.record_updated()` exactly once; otherwise report no update
   d. If the IBS query fails, log the error, call
      `self.record_failed()`, and continue to the next package
   e. Respect `request_delay` from `FetcherConfig` between IBS API
      calls via `asyncio.sleep(self.config.request_delay)`

### Operation 3: Repair

Populate bugowner data for packages in active tickets that are missing
from the cache.

1. Query distinct package names that have a non-deleted `TicketPackage` and at
   least one `TicketPackageTrack.workflow_type = ibs` under an active Ticket.
   Lifecycle actionability does not filter this repair scope
2. For each `package_name` that does NOT have a corresponding
   `PackageBugowner` record:
   a. Execute the [Bugowner Resolution Algorithm](#bugowner-resolution-algorithm)
   b. Call `self.record_created()` for each new record
   c. If the IBS query fails, log the error, call
      `self.record_failed()`, and continue to the next package
   d. Respect `request_delay` from `FetcherConfig` between IBS API
      calls via `asyncio.sleep(self.config.request_delay)`

### Execution Order

The three operations MUST execute in the order listed (cleanup, update,
repair). This ensures:

- Cleanup runs first so that removed packages are not needlessly updated
- Update runs before repair so that existing entries are refreshed before
  new ones are created (avoids querying IBS twice for a package that
  already has a stale entry)

## API Endpoints

### List Tickets (extended)

The existing `GET /api/v1/tickets` endpoint is extended with a new
filter parameter:

```
GET /api/v1/tickets?bugowner={email_or_name}
```

- `bugowner` (string, optional): filter tickets to those containing at
  least one package whose `PackageBugowner.bugowner_email` or
  `PackageBugowner.bugowner_name` matches the value. For group members,
  also matches if the value corresponds to a
  `PackageBugownerMember.email` or `PackageBugownerMember.userid`.

This enables queries like:
- "All tickets for packages maintained by `kernel-team@suse.de`"
- "All tickets for packages where `jdoe` is the bugowner or a
  member of the bugowner group"

### Ticket Detail (extended)

The existing `GET /api/v1/tickets/{id}` response is extended to include
bugowner information in the package data. For each package in the
ticket, the response includes:

```json
{
  "package_name": "curl",
  "bugowner": {
    "type": "group",
    "name": "pkg-maintainers",
    "email": "pkg-maintainers@suse.de",
    "members": [
      {"userid": "asmith", "email": "alice.smith@suse.com"},
      {"userid": "bwilson", "email": "bob.wilson@suse.com"},
      {"userid": "cjones", "email": "carol.jones@suse.com"}
    ]
  }
}
```

For a person bugowner:

```json
{
  "package_name": "apache2",
  "bugowner": {
    "type": "person",
    "name": "jdoe",
    "email": "john.doe@suse.com",
    "members": null
  }
}
```

If the bugowner is unknown (resolution failed or not yet populated):

```json
{
  "package_name": "some-package",
  "bugowner": null
}
```

The `members` field is included only when `type` is `group`. For
`person` bugowners, `members` is `null`.

No additional authentication is required — bugowner information is
visible to all users (same access level as package data in tickets).

## Background Tasks

- `sync_ibs_bugowners`: runs every 14 days at 03:00 UTC. Performs
  cache maintenance (cleanup, update, repair). Inherits from
  `BaseFetcher`. See [Maintenance Fetcher](#maintenance-fetcher) for
  details.

## Security

- Bugowner data is read-only in Sentinel — it is fetched from IBS and
  cannot be edited by any user
- Bugowner information is visible to all users (no role required), as
  it is non-sensitive organizational data
- The `sync_ibs_bugowners` fetcher configuration (enable/disable,
  schedule, rate limit) requires the `manage_fetchers` capability, managed
  via the fetcher dashboard like all other fetchers. See
  `docs/features/identity/rbac.md`

## Future Considerations

- **Maintainer dashboard**: a dedicated page where each maintainer can
  see all active tickets for packages they maintain, with submission
  status and progress tracking. Will be specified in a separate feature
  spec.
- **Source strategy**: determine whether IBS is authoritative alone or whether
  Git metadata participates as a fallback, how conflicting answers are
  resolved, what makes a cache record usable, and whether a multi-source
  strategy requires renaming the current source-specific fetcher. The result
  remains one bugowner per package.

## Cross-references

- `docs/api-spec.md` — global API conventions (envelope format, error codes,
  pagination, shared 422 responses)
- `docs/data-model.md` — full database schema (bugowner tables)
- `docs/features/packages/package-model.md` — `TicketPackage` model,
  package lifecycle context
- `docs/features/integrations/ibs-integration.md` — IBS REST API endpoints
  (bugowner resolution endpoint)
- `docs/features/identity/rbac.md` — access control for fetcher operations
