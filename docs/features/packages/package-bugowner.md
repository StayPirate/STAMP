# Package Bugowner Tracking

## Purpose

Track the IBS bugowner (package maintainer) for each source package
referenced in Sentinel tickets. The bugowner is the person or group
responsible for maintaining a package in IBS, and is the primary contact
for coordinating security update submissions.

This feature enables:

1. Identifying who is responsible for preparing and submitting fixes for
   each package in a ticket
2. Filtering tickets by bugowner (e.g., "show me all active tickets for
   packages I maintain")
3. Future integration with a maintainer dashboard where each maintainer
   can see pending submissions and track progress (separate spec)
4. Future notification system to alert maintainers about new tickets
   affecting their packages (separate spec)

This specification is the authoritative source for bugowner resolution,
caching, and maintenance. See `docs/features/packages/package-model.md` for
package affectedness and release tracking. See
`docs/features/integrations/ibs-integration.md` for IBS API integration details.

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

1. **On-demand update**: when a package is added to a ticket, Sentinel
   queries IBS for the current bugowner and creates or updates the
   cache entry
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

Caches the current IBS bugowner for each source package actively tracked
in Sentinel tickets. This is a shared cache — all tickets referencing the
same package point to the same bugowner record.

Records are created on-demand when a package is first added to a ticket,
and maintained by the periodic `sync_ibs_bugowners` fetcher.
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

When a package is added to a ticket (via `add_package_to_ticket`), Sentinel
resolves the bugowner as follows:

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
   `User.email` (also stored as lowercase from AD sync) without
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
| Scope | All `PackageBugowner` records + packages in active tickets missing from the cache |
| Auth | HTTP Basic / API token (internal) |
| Custom settings | No |

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

### Operation 2: Update

Refresh bugowner data from IBS for all remaining `PackageBugowner`
records.

1. For each `PackageBugowner` record not removed in Operation 1:
   a. Execute the [Bugowner Resolution Algorithm](#bugowner-resolution-algorithm)
      for the `package_name`
   b. If the bugowner has changed (different type, name, or email),
      update the record and call `self.record_updated()`
   c. If the bugowner is a group, synchronize the member list: add new
      members, remove departed members, update changed emails
   d. If the IBS query fails, log the error, call
      `self.record_failed()`, and continue to the next package
   e. Respect the `rate_limit` from `FetcherConfig` between IBS
      requests (default: no limit — admin-configurable via the fetcher
      dashboard)

### Operation 3: Repair

Populate bugowner data for packages in active tickets that are missing
from the cache.

1. Query all distinct `package_name` values from
   `TicketPackage` where `TicketPackage.deleted_at IS NULL` and the
   parent `Ticket` is **active**
2. For each `package_name` that does NOT have a corresponding
   `PackageBugowner` record:
   a. Execute the [Bugowner Resolution Algorithm](#bugowner-resolution-algorithm)
   b. Call `self.record_created()` for each new record
   c. If the IBS query fails, log the error, call
      `self.record_failed()`, and continue to the next package
   d. Respect the `rate_limit` from `FetcherConfig` between IBS
      requests

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
  schedule, rate limit) is admin-only, managed via the fetcher dashboard
  like all other fetchers. See `docs/features/identity/rbac.md`

## Future Considerations

- **Maintainer dashboard**: a dedicated page where each maintainer can
  see all active tickets for packages they maintain, with submission
  status and progress tracking. Will be specified in a separate feature
  spec.
- **Notification system**: automated notifications to bugowners when new
  tickets are created for their packages, or when ticket status changes
  require their attention. Will be specified in a separate feature spec.

## Cross-references

- `docs/api-spec.md` — global API conventions (envelope format, error codes,
  pagination, shared 422 responses)
- `docs/data-model.md` — full database schema (bugowner tables)
- `docs/features/packages/package-model.md` — `TicketPackage` model,
  package lifecycle context
- `docs/features/integrations/ibs-integration.md` — IBS REST API endpoints
  (bugowner resolution endpoint)
- `docs/features/identity/rbac.md` — access control for fetcher operations

