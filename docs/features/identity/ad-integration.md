# Active Directory Integration

## Purpose

Synchronize SUSE employee data from Active Directory into Sentinel to enable
user pre-provisioning, autocomplete search, package bugowner enrichment,
automatic role assignment from AD group membership, and manager-based
ticket escalation when employees leave the organization.

The LDAP sync fetcher is the **sole authority** for identity data of AD
users (`username`, `email`, `full_name`, `manager_id`,
`ad_synced_at`). These fields cannot be modified manually via the API or
CLI. See AD User Data Ownership in `docs/features/identity/user-service.md`
for the service-layer enforcement rules.

## Data Source

Sentinel uses the SUSE Active Directory as the single source of truth for
employee identity data. The LDAP endpoint at `pan.suse.de` is an OpenLDAP
proxy (`back-ldap` backend with `pcache` overlay) that forwards queries to
the underlying Microsoft Active Directory domain controllers. See
`docs/data-sources.md` for the full infrastructure description.

From Sentinel's perspective the proxy is mostly transparent — it speaks
standard LDAPv3 and relays AD attributes and errors — but it affects
schema discovery, caching of "live" queries, and TLS scope (see
Implementation Notes below).

- **Server**: `ldaps://pan.suse.de`
- **Base DN**: `OU=User accounts,DC=corp,DC=suse,DC=com`
- **Authentication**: anonymous bind (no credentials required)
- **Protocol**: LDAPS (port 636, TLS). The server certificate is issued by
  the SUSE internal PKI (chain: SUSE CA all 2023.1 → SUSE CA Root → SUSE
  Trust Root). The root CA certificate is committed at
  `certs/SUSE_Trust_Root.crt` and installed in the container system trust
  store at build time. The `LDAP_URI` and `LDAP_CA_CERT_PATH` environment
  variables control the connection (see `docs/configuration.md`).

### Security rationale

TLS is mandatory for this connection despite anonymous bind and internal
network placement. The `MEMBEROF` attribute returned by AD is used to
derive Sentinel role assignments via Role Mappings — including the `admin`
role. Without TLS, a man-in-the-middle attacker on the network path could
inject forged `MEMBEROF` values in LDAP responses, causing the sync process
to grant arbitrary roles (including `admin`) to attacker-controlled
accounts. LDAPS with server certificate validation ensures response
authenticity and eliminates this privilege escalation vector.

**TLS scope**: the LDAPS connection terminates at the OpenLDAP proxy
(`pan.suse.de`). The connection between the proxy and the backend AD
domain controllers is managed by the proxy infrastructure and is outside
Sentinel's control. The MITM protection described above covers the
Sentinel → proxy segment. The proxy → AD segment is on the SUSE
internal network and managed by SUSE infrastructure; compromising it
would require internal network access beyond the application's threat
model.

### Attributes consumed

| AD Attribute        | Sentinel Field      | Description                          |
|---------------------|---------------------|--------------------------------------|
| `objectGUID`        | `ad_object_guid`  | Immutable AD identifier (UUID). Used as the stable matching key during sync |
| `sAMAccountName`    | `username`          | Username (e.g., `jdoe`). Updated on every sync if changed in AD |
| `cn`                | `full_name`         | Full display name                    |
| `mail`              | `email`             | Primary email (`.com`)               |
| `manager`           | `manager_id`        | DN of the direct line manager (resolved to `user.id` FK) |
| `EMPLOYEESTATUS`    | `active`            | Employee status (`Active` or other; absent = skipped)  |
| `distinguishedName` | *(not persisted)*   | Fetched for in-memory manager resolution (DN to `objectGUID` mapping) and diagnostic logging. Not stored in the database |

The `MEMBEROF` attribute is read during sync to apply role mappings but
is not persisted in the database.

## Data Model

See `docs/data-model.md` for the complete schema of the User, UserRole,
and RoleMapping tables. The AD-specific columns (`ad_object_guid`,
`manager_id`, `ad_synced_at`) are documented there along with
the `ad_group_cn` and `assigned_by` columns on UserRole. The Attributes
Consumed table above shows how AD attributes map to these fields during
sync.

## Fetcher

### `sync_ldap_directory`

A `BaseFetcher` subclass registered in the fetcher dashboard.

- **Name**: `sync_ldap_directory`
- **Description**: Syncs SUSE employee data from Active Directory
- **Default schedule**: daily at 04:00 UTC

**Custom Settings**

This fetcher declares the following custom settings (see
`docs/features/platform/fetcher-infrastructure.md`, "Custom Settings
Schema" for the schema structure and validation rules):

| Setting | Type | Default | Range | Warning | Description |
|---------|------|---------|-------|---------|-------------|
| `max_deactivations` | int | 20 | 1–100 | Yes | Maximum number of users that can be deactivated in a single sync run. If exceeded, the sync aborts as a safety measure |
| `ldap_connect_timeout` | int | 30 | 5–120 | — | LDAP connection timeout in seconds (TCP/TLS handshake) |
| `ldap_operation_timeout` | int | 120 | 30–600 | — | LDAP search operation timeout in seconds |
| `retry_max_attempts` | int | 3 | 1–5 | — | Total LDAP connection attempts on transient failure (initial + retries) |

Warning text for `max_deactivations`: "Safety threshold. Raising this
value allows more users to be deactivated in a single sync run. Change
only if you understand the impact."

#### Sync algorithm

1. **Query AD**: fetch all entries under
   `OU=User accounts,DC=corp,DC=suse,DC=com` with attributes
   `objectGUID`, `sAMAccountName`, `cn`, `mail`, `manager`,
   `EMPLOYEESTATUS`, `distinguishedName`, `MEMBEROF`. No
   `EMPLOYEESTATUS` filter is applied — the query returns all users
   (active and inactive) so that pre-flight checks can operate on
   the complete dataset. The query MUST use the LDAP Simple Paged
   Results Control (RFC 2696) with a page size of 500 entries. All
   pages MUST be fetched before proceeding to step 2. This is
   required because Active Directory enforces a server-side size
   limit (currently 5,000 entries for anonymous binds) — a non-paged
   query will silently truncate the result set if the directory grows
   beyond this limit, which would cause the pre-flight checks to
   misidentify the truncation as a data quality issue.

   **Retry on transient LDAP failures**: if the LDAP connection or
   query fails with a connection timeout or operation timeout, the
   fetcher retries internally before propagating the failure:
   - Maximum attempts controlled by the `retry_max_attempts` custom
     setting (default: **3** total attempts, configurable 1–5)
   - Delay between attempts uses exponential backoff: **30 seconds**
     after the 1st failure, **60 seconds** after the 2nd failure
     (delays scale proportionally if `retry_max_attempts` is changed)
   - Log INFO at each retry: `"LDAP query failed (attempt {n}/{max}),
     retrying in {delay}s: {error_message}"`
   - If all attempts fail, the exception propagates to
     `BaseFetcher.run()` which marks the run as `failure`
   - Only connection timeouts and operation timeouts trigger retries.
     Other exceptions (e.g., TLS certificate validation failure,
     authentication errors) fail immediately without retry — these
     indicate configuration problems, not transient issues

   After pre-flight checks pass (step 2), entries with
   `EMPLOYEESTATUS != Active` are used only for building the
   deactivation candidate list (step 3) and are excluded from the
   upsert, manager resolution, and role mapping steps (steps 3–5)
2. **Pre-flight checks** (all evaluated before any database modification):
   three safety checks run in sequence. If ANY check fails, the entire
   sync aborts immediately with status `failure` — no partial execution,
   no database writes.

   **Level 1 — Missing User Detection (hard block)**:
   - Compare all `ad_object_guid` values of existing AD users in Sentinel
     against those present in the AD results
   - If ANY previously known user (by `ad_object_guid`) is absent from
     the AD results → ABORT the entire sync
   - Rationale: in SUSE AD, users are never deleted — they are marked
     inactive via `EMPLOYEESTATUS` but remain in the directory. A user
     disappearing entirely from results indicates a technical problem
     (LDAP result truncation, replication lag, network issue) or an AD
     policy violation. Because the query fetches all users regardless of
     `EMPLOYEESTATUS`, a missing user cannot be explained by a status
     change — it is always anomalous
   - Log ERROR: `"Pre-flight check failed: {n} previously known users
     missing from AD results. Missing ad_object_guid values:
     [{guid_list}]. Aborting sync — this likely indicates an LDAP
     infrastructure issue."`

   **Level 2 — Group Membership Sanity (hard block)**:
   - Check whether ANY user in the AD results has at least one
     `MEMBEROF` entry
   - If ZERO users have any `MEMBEROF` entries → ABORT the entire sync
   - Rationale: in a corporate AD like SUSE, it is statistically
     impossible for no employee to belong to any group. Zero group
     memberships indicates the `MEMBEROF` attribute is not being
     returned (LDAP server misconfiguration, replication issue)
   - Log ERROR: `"Pre-flight check failed: 0 out of {total} users have
     MEMBEROF entries. Aborting sync — MEMBEROF attribute is likely not
     being returned by the LDAP server."`

   **Level 3 — Mass Deactivation Threshold (configurable block)**:
   - Count users present in the AD results whose `EMPLOYEESTATUS`
     indicates inactivity (i.e., `EMPLOYEESTATUS != Active`) but who
     are currently `active = true` in Sentinel
   - If count exceeds the `max_deactivations` custom setting (default:
     **20**, configurable 1–100 via the admin dashboard) → ABORT the
     entire sync
    - Entries with a missing `EMPLOYEESTATUS` attribute are excluded from
      this count — only entries with an explicit non-`Active` value are
      considered deactivation candidates
    - Rationale: because the query returns all users (active and
     inactive), this check can accurately count how many existing
     Sentinel users would be deactivated in this sync cycle. An
     anomalously high number suggests a mass status change in AD
     (organizational restructuring, data migration error) that should
     be reviewed before processing
   - Log ERROR: `"Pre-flight check failed: {n} users would be
     deactivated (threshold: {max}). Affected usernames:
     [{username_list}]. Aborting sync — review manually and increase
     max_deactivations via the fetcher configuration if intentional."`

   **Step 2 flow**:
   1. Run Level 1 check → if fails, log + abort
   2. Run Level 2 check → if fails, log + abort
   3. Run Level 3 check → if fails, log + abort
   4. All checks pass → proceed to step 3

   **Design rationale — deactivation threshold protection**: the
   `max_deactivations` setting is a custom setting (configurable via
   the admin dashboard) rather than a static environment variable
   requiring a worker restart. This provides operational flexibility
   while maintaining adequate safety through multiple layers:
   - **Bounded range** (1–100): the schema enforces hard limits,
     preventing an admin from disabling the threshold entirely or
     setting it to an unreasonably high value
   - **Visual warning**: the admin UI displays a persistent safety
     warning (yellow triangle + amber box) explaining the impact of
     changing this value, ensuring the admin makes an informed decision
   - **Audit trail**: every change is recorded in `FetcherAuditLog`
     with old and new values, providing accountability
   - **Immediate effect**: the updated value takes effect on the next
     sync run without requiring a worker restart, which is advantageous
     in legitimate scenarios (e.g., a known mass departure) where the
     admin needs to adjust the threshold, run the sync, verify the
     result, and restore the original value — all through the dashboard

   **Design rationale — all-or-nothing**: the pre-flight checks use an
   all-or-nothing strategy rather than partial execution. If any check
   indicates a data quality issue, running the sync with restrictions
   (e.g., skipping deactivations but processing other changes) could
   produce an inconsistent state — users updated with stale role data,
   managers resolved against an incomplete dataset, etc. A full abort
   is safer: the previous sync's data remains intact, and the admin
   can investigate the root cause before re-running.

   **No retry on pre-flight failures**: pre-flight check failures
   indicate data quality or infrastructure issues that require manual
   investigation — they are not transient. The retry mechanism in step 1
   covers only LDAP connection/operation timeouts. If a pre-flight check
   fails, the sync aborts immediately without retry.

   **First sync (empty database)**: on the very first sync run, the
   Sentinel database contains zero AD users. Level 1 (Missing User
   Detection) and Level 3 (Mass Deactivation Threshold) pass vacuously
   — there are no previously known users to be missing and no existing
   active users to be deactivated. This is the intended behavior: there
   is no data to protect yet. Level 2 (Group Membership Sanity) operates
   normally because it inspects only the AD results, not the database.
    **Step 2b — Entry normalization**: after all pre-flight checks pass,
    the complete AD result set is validated and separated into three
    disjoint sets. The order of checks matters — `EMPLOYEESTATUS` is
    evaluated first because entries without it should not be classified
    as active or inactive.

    For each entry in the AD result set:
    1. If `EMPLOYEESTATUS` is missing → add to **skipped** set. Log
       WARNING: `"AD entry '{sAMAccountName or objectGUID}' has no
       EMPLOYEESTATUS — skipping."` Call `record_failed()`
    2. If `EMPLOYEESTATUS` is present and `!= Active` → add to
       **inactive entries** set
    3. If `EMPLOYEESTATUS == Active` → check required attributes
       (`objectGUID`, `sAMAccountName`, `mail`). If any required
       attribute is missing or empty (after trimming whitespace) → add
       to **skipped** set. Log WARNING identifying the entry by whatever
       attributes ARE present (e.g., `"Skipping AD entry: missing
       objectGUID (DN: '{distinguishedName}')."` or `"AD entry has no
       sAMAccountName — skipping (ad_object_guid: {objectGUID})."` or
       `"Skipping AD entry '{sAMAccountName}': missing or empty mail
       attribute (User.email has a UNIQUE NOT NULL constraint)."`). Call
       `record_failed()` for each skipped entry
    4. If `EMPLOYEESTATUS == Active` and all required attributes are
       present → add to **active entries** set

    After processing all entries, log an aggregated report at INFO level:
    `"Entry normalization: {N} active, {M} inactive, {K} skipped
    ({details})."` where `{details}` summarizes skip reasons (e.g.,
    `"2 missing EMPLOYEESTATUS, 1 missing mail"`).

    The three sets are consumed by subsequent steps:
    - **Active entries** → steps 3 (upsert), 4 (manager resolution),
      5 (role mappings)
    - **Inactive entries** → steps 6 (deactivation) and 7 (reactivation)
      for building candidate lists
    - **Skipped entries** → not processed further (already counted via
      `record_failed()`)

    Pre-flight checks (step 2) operate on the complete, unfiltered AD
    result set. Normalization runs after pre-flight checks pass and
    before any database modification.

    **Design note**: entry normalization is complementary to the Level 1
    pre-flight safety check. Level 1 detects wholesale data loss (known
    users disappearing from the result set), while normalization catches
    individual entry corruption (e.g., proxy issues, replication lag,
    corrupted entries) that Level 1 is blind to.

3. **Upsert users**: process only the **active entries** set from step
    2b (entries with `EMPLOYEESTATUS == Active` and all required
    attributes validated). For each active entry:
    - If a `User` record with matching `ad_object_guid` exists, update
     `username`, `full_name`, `email`, `ad_synced_at` via
     `user_service.update_user()`. The `active` field is NOT modified
     in this step — deactivations and reactivations are handled in
     steps 6 and 7 respectively
     - If `update_user()` raises `UserConflictError` (e.g., the AD
       `sAMAccountName` changed to a value that collides with another
       existing user's username or email), `UsernameFormatError` (e.g.,
       the new `sAMAccountName` fails Sentinel's username format rules),
       or `UserNotFoundError` (race condition — user was deleted between
       lookup and update), log WARNING:
       `"Cannot update AD user '{ad_object_guid}': {error_message}."`,
       call `record_failed()`, and skip this entry. The admin must
       resolve the conflict manually and re-run the sync. The sync
       continues processing remaining entries. Any other exception from
       `update_user()` (e.g., `ADUserFieldReadOnlyError`) indicates a
       bug in the sync caller and is NOT caught — it propagates to
       `BaseFetcher.run()` to abort the sync
   - If no matching record exists, attempt to create a new `User` via
     `user_service.create_user()` with `username = sAMAccountName`,
     `email = mail`, `full_name = cn`,
      `ad_object_guid = objectGUID`,
      `active = true`, `acting_user_id = None`.
     Note: only active AD entries reach this step, so new users are
     always created as active
     - If `create_user()` raises `UserConflictError` (e.g., the AD
       `sAMAccountName` collides with an existing local user's username
       or email) or `UsernameFormatError` (e.g., the AD
       `sAMAccountName` contains characters not allowed by Sentinel's
       username format rules), log WARNING:
       `"Cannot create AD user '{ad_object_guid}': {error_message}."`,
       call `record_failed()`, and skip this entry. The admin must
       resolve the conflict manually (e.g., rename or remove the local
       user, or merge accounts). The sync continues processing
       remaining entries. Any other exception from `create_user()`
       (e.g., `ADUserPasswordError`) indicates a bug in the sync caller
       and is NOT caught — it propagates to `BaseFetcher.run()` to
       abort the sync

   After upsert, build the deactivation and reactivation candidate
    lists from the **inactive entries** set (step 2b) and the **active
    entries** set (step 2b):
    - **`newly_deactivated`**: existing AD users (`ad_object_guid IS NOT
      NULL`) who are currently `active = true` in Sentinel and appear in
      the **inactive entries** set
    - **`newly_reactivated`**: existing AD users who are currently
      `active = false` in Sentinel and appear in the **active entries**
      set
    - No `active` field writes happen in this step
4. **Resolve managers** (two-pass): after all users have been
   created/updated in step 3, resolve manager relationships. For each
   user with a `manager` DN in AD:
   - Look up the manager's DN in the current sync batch to find the
     corresponding `objectGUID`
   - Find the User record with that `ad_object_guid` and resolve the
     target `manager_id` (the manager's `user.id`)
   - If the manager is not found in the User table (e.g., the manager
     is outside the synced OU), the target `manager_id` is `NULL`
   - Call `user_service.update_user(user_id=user.id,
     manager_id=resolved_manager_id, acting_user_id=None)` to apply
     the resolved value. If the user's `manager_id` is already correct,
     `update_user()` treats it as a no-op
   - Note: `manager_id` may point to an inactive user — this is by
     design, as the reporting relationship in AD persists regardless of
     `EMPLOYEESTATUS`
5. **Apply role mappings** (delegated to user service): for each
   `RoleMapping(ad_group_cn, role)` in the database:
   - Identify all users whose `MEMBEROF` (from the AD data already in
     memory) includes this mapping's `ad_group_cn`. Matching extracts
     the first `CN=` component from each `MEMBEROF` DN using a
     standards-compliant DN parser (e.g., `ldap3.utils.dn.parse_dn()`)
     and compares it to `ad_group_cn` **case-insensitively** (consistent
     with Active Directory's case-insensitive CN semantics). Example: a
     `MEMBEROF` value of
     `CN=O SUSE Security,OU=Groups,DC=corp,DC=suse,DC=com` matches
     `ad_group_cn = "O SUSE Security"` because the extracted CN
     (`O SUSE Security`) equals the mapping value under case-insensitive
     comparison.
     **Design note**: matching on the CN component alone (rather than
     the full DN) carries a theoretical risk of collision if two groups
     in different OUs share the same CN. In the current SUSE AD (~85
     relevant groups), zero such collisions exist. Full-DN matching
     support is deferred unless a real collision emerges
   - Call `user_service.sync_role_mapping(role, ad_group_cn,
     current_member_user_ids, acting_user_id=None)`. The service
     creates `UserRole` records for users newly in the group and
     removes records for users no longer in the group (see
     `docs/features/identity/user-service.md` for the full contract)
   - Each mapping operates exclusively on records tagged with its own
     `ad_group_cn`. Manual roles (`_manual`) and records from other
     mappings are never touched. Processing order is irrelevant
   - For the full semantics of how AD-derived and manual role
     assignments coexist independently, see `docs/features/identity/rbac.md`
     (Role Origins and Coexistence)
6. **Deactivation side effects**: for each user in the
    `newly_deactivated` list (built in step 3 from the **inactive
    entries** set produced by step 2b), call
   `user_service.deactivate_user()` with
    `reason = "employee deactivated in Active Directory"` and
   `acting_user_id = None`. The service sets `active = false` and
   executes all side effects atomically. See
     `docs/features/identity/user-service.md` for the full contract (ticket
     unassignment, API key revocation, TicketEvent creation)
7. **Reactivation**: for each user in the `newly_reactivated` list
    (built in step 3 from the **active entries** set produced by step
    2b), call `user_service.reactivate_user()` with
   `acting_user_id = None`. See `docs/features/identity/user-service.md` for
     reactivation semantics (previously unassigned tickets and API keys
    are NOT restored)
8. **Metrics**: report `record_created()` for new users,
   `record_updated()` for updated users (including deactivations and
   reactivations from steps 6–7), `record_failed()` for entries that
   failed processing

   **Step ordering rationale (5→6→7)**: steps 5, 6, and 7 operate on
   independent data and produce independent side effects. Step 5
   manages `UserRole` records (no TicketEvents). Step 6 manages
   `active` status, API keys, sessions, and ticket assignments (with
   TicketEvents). Step 7 sets `active = true` (no TicketEvents). Roles
   are not affected by deactivation or reactivation — they persist
   across status changes. The `newly_deactivated` and
   `newly_reactivated` lists are mutually exclusive by construction
   (a user cannot be both `active = true` and `active = false` in the
   DB). Therefore the ordering of these steps does not affect
    correctness or TicketEvent content.

#### Transaction boundaries

Steps 3–7 use **per-service-call transactions** — each invocation of a
`user_service` function (`create_user`, `update_user`, `deactivate_user`,
`reactivate_user`, `sync_role_mapping`) commits independently. There is
no single transaction wrapping the entire sync run.

**Crash recovery**: if the sync process crashes mid-execution (e.g.,
after upserting 1,500 of 3,200 users), the database contains a partially
updated state — some users reflect the latest AD data while others
retain data from the previous sync. This is safe because:

1. **Idempotency** (Business Rule 8): re-running the sync with the same
   AD data produces the same final state regardless of the starting
   point. Users already processed are updated to the same values (no-op),
   and unprocessed users are brought up to date
2. **No cross-user dependencies within a step**: each user's upsert,
   manager resolution, role mapping, deactivation, or reactivation is
   independent of other users' processing state. A partially completed
   step does not leave any individual user in an inconsistent state
3. **Manager resolution convergence**: step 4 (manager resolution) may
   set `manager_id = NULL` for users whose manager was not yet upserted
   in a partial run. On re-run, the manager will exist and the FK is
   resolved correctly

A single wrapping transaction is not used because: (a) the sync
processes thousands of users — holding a transaction open for the full
duration would risk lock contention, memory pressure, and long rollback
times on failure; (b) the skip-and-continue error handling in step 3
(logging a warning and proceeding to the next entry) is incompatible
with a single transaction that rolls back entirely on failure.

This model contrasts with the role mapping CRUD endpoints (POST / DELETE
`/role-mappings`), which wrap all their operations — rule persistence,
user re-evaluation, and TicketEvent creation — in a **single atomic
transaction**. CRUD operations are short, admin-initiated, and expected
to either succeed fully or roll back entirely.

#### Active status ownership

Active Directory `EMPLOYEESTATUS` is the sole source of truth for the
`active` field on AD users. Manual deactivation or reactivation of
AD users by admins (via API, CLI, or UI) is blocked by both the
service layer (`ADUserStatusReadOnlyError`) and CLI-level guards. Only
the LDAP sync fetcher may call `deactivate_user()` and
`reactivate_user()` for AD users (passing `acting_user_id = None`).

If an AD user must be blocked from accessing Sentinel, deactivate the
employee in Active Directory. The next sync cycle will propagate the
change with all associated side effects (API key revocation, session
invalidation, ticket unassignment).

See `docs/features/identity/user-service.md` (AD Active Status
Ownership) for the full rationale and enforcement details.

#### Username rename impact

If an employee's `sAMAccountName` changes in Active Directory, the sync
updates the `username` field in Sentinel. This has the following
implications:

- **External references break**: bookmarked URLs, external links, or
  cached API responses that use the old username will return 404. This
  is a known side-effect of the sync — username stability is not
  guaranteed
- **Internal identification is not affected**: all database
  relationships, tickets, historical data, and TicketEvent records use
  UUID primary keys, not usernames. No data integrity is lost
- **Active sessions are not affected**: JWT tokens use UUID as the
  subject (`sub` claim), not the username. Existing sessions continue
  to work after a rename
- **Tracking**: the rename is recorded in the fetcher execution log via
  `record_updated()` and a log entry reporting the old and new username
  (e.g., `"Username changed: 'jdoe' → 'jsmith'"`)

#### Manager resolution

The `manager` attribute in AD contains a full DN (e.g.,
`cn=John Doe,ou=User accounts,dc=corp,dc=suse,dc=com`). During
sync, the fetcher resolves this to a `manager_id` foreign key by:

1. Looking up the DN in the current sync batch to find the
   corresponding `objectGUID` (preferred, avoids extra AD queries)
2. Finding the User record with that `ad_object_guid` and using its
   `id` as `manager_id`

The `manager_id` field is a proper foreign key to `user.id`. Since
manager resolution runs as a second pass after all users have been
created/updated (step 4), the manager record is guaranteed to exist if
they are in the synced OU. If the manager is outside the synced OU (e.g.,
a senior executive in a different organizational unit), `manager_id` is
set to NULL.

## Concurrency Considerations

### Role mapping creation during a running sync

If an admin creates a role mapping (POST /api/v1/admin/role-mappings) while a
sync is in progress, the current sync may not process the new mapping in
step 5 — depending on whether step 5 has already iterated past the point
where the new RoleMapping record was inserted.

This does not cause inconsistency: the POST endpoint applies roles immediately
to all matching users (processing step 3), so affected users receive the role
at creation time. The next scheduled sync will reconcile any users that were
missed (e.g., users created between the POST and the next sync).

No locking mechanism is needed between role mapping creation and the sync
process.

## CLI Usage

The LDAP sync can be triggered from the command line using the generic
fetcher command:

```
sentinel fetcher run sync_ldap_directory
```

This runs the sync synchronously in the CLI process (no Celery
required). See `docs/features/platform/fetcher-dashboard.md` (section "CLI
Commands") for full details on the `sentinel fetcher` command group.

### Post-deployment bootstrap sequence

```
1. sentinel fetcher run sync_ldap_directory                        # populate User table (~3,200 records)
2. sentinel manage-user update --username admin1 --add-role admin  # assign Admin role to first admin
```

The `manage-user` command is documented in
`docs/features/identity/user-management.md`. In this bootstrap context, the
user already exists (created by the LDAP sync in step 1), and
`manage-user update` adds the Admin role with `ad_group_cn = '_manual'`
and `assigned_by = NULL` (CLI action).

## API Endpoints

### User search and autocomplete

```
GET /api/v1/users
```

Public endpoint (read-only). The full endpoint specification is defined
in `docs/features/identity/user-management.md` (Public API endpoints).

### User detail

```
GET /api/v1/users/{user}
```

Public endpoint (read-only). The full endpoint specification is defined
in `docs/features/identity/user-management.md` (Public API endpoints).

### User role management

```
POST /api/v1/admin/users/{user}/roles
```

Admin only. Add or remove manual roles for a user. The full endpoint
specification (request/response schema, validation rules, error codes)
is defined in `docs/features/identity/user-management.md` (Admin API endpoints).

Key rules (defined in detail in user-management.md):
- Cannot remove AD-derived roles (only manual roles are removable)
- Cannot remove your own Admin role
- Adding an existing role is idempotent

### Role Mapping management

### List Role Mappings

```
GET /api/v1/admin/role-mappings
```

Admin only. Returns all configured role mappings.

**Pagination**: not paginated. The number of role mappings is naturally
bounded (one per AD group × role combination, expected <30 entries).
The full list is always returned.

**Sorting**: results are returned in insertion order (`created_at`
ascending). No client-side sorting parameters are supported (bounded
dataset).

Response:
```json
{
  "data": [
    {
      "id": "uuid",
      "ad_group_cn": "O SUSE Security",
      "role": "vulnerability_analyst",
      "created_by": { "id": "uuid", "username": "admin1", "full_name": "..." },
      "created_at": "2024-01-01T00:00:00Z"
    }
  ]
}
```

### Preview Role Mapping

```
POST /api/v1/admin/role-mappings/preview
```

Admin only. Queries AD live to show which users would be affected by a
proposed mapping. Does not persist anything.

Request body:
```json
{
  "ad_group_cn": "O SUSE Security",
  "role": "vulnerability_analyst"
}
```

Response:
```json
{
  "data": {
    "ad_group_cn": "O SUSE Security",
    "role": "vulnerability_analyst",
    "affected_users": [
      { "id": "uuid", "username": "jdoe", "full_name": "John Doe", "email": "..." },
      { "id": "uuid", "username": "asmith", "full_name": "Alice Smith", "email": "..." }
    ],
    "affected_count": 22,
    "unknown_users": ["newemployee"]
  }
}
```

The `unknown_users` field lists AD usernames found in the group but not
yet present in the User table (e.g., employees hired after the last
sync). These users will receive the role at the next sync.

**Zero-member group**: if the AD group exists but currently has zero
members, the response is valid with `affected_users: []`,
`unknown_users: []`, and `affected_count: 0`. This is not an error — an
admin may create a role mapping for a group that is not yet populated, in
preparation for future members.

**UI rendering notes**:

- The `unknown_users` list is displayed in the UI only when non-empty.
  When empty, the section is hidden to avoid confusing administrators.
- When displayed, a tooltip (info icon or mouse-over) provides a brief
  explanation: "Users found in the AD group but not yet synced to
  Sentinel. They will receive the role at the next directory sync."

**Validation**:

- `ad_group_cn` MUST contain only characters valid for an Active Directory
  group CN: letters (any script), numbers, spaces, hyphens, underscores,
  and dots. Values containing any other characters (including LDAP
  metacharacters `*`, `(`, `)`, `\`, NUL per RFC 4515) MUST be rejected
  with 422 / `ROLE_MAPPING_INVALID_GROUP_CN`

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 422 | `VALIDATION_ERROR` | Invalid request body (missing or empty `ad_group_cn`, unrecognized `role`) |
| 422 | `ROLE_MAPPING_INVALID_GROUP_CN` | `ad_group_cn` contains characters invalid for an AD group CN |
| 503 | `AD_UNAVAILABLE` | AD is unreachable or the connection timed out (10–15 s timeout) |

### Create Role Mapping

```
POST /api/v1/admin/role-mappings
```

Admin only. Creates a new role mapping. The roles are applied immediately
to all matching users (not deferred to the next sync).

Request body:
```json
{
  "ad_group_cn": "O SUSE Security",
  "role": "vulnerability_analyst"
}
```

Validation:
- Returns 422 with code `ROLE_MAPPING_INVALID_GROUP_CN` if `ad_group_cn`
  contains characters invalid for an AD group CN. Valid characters:
  letters (any script), numbers, spaces, hyphens, underscores, and dots.
  This rejects LDAP metacharacters (`*`, `(`, `)`, `\`, NUL per RFC 4515)
  and any other special characters at the input boundary, preventing LDAP
  injection before the value is used in any query
- Returns 422 with code `VALIDATION_ERROR` if `ad_group_cn` exceeds 256
  characters (maximum length matching Active Directory CN limits)
- Returns 422 with code `ROLE_MAPPING_GROUP_NOT_FOUND` if the AD group
  does not exist (queries AD live to verify). This is a business-level
  validation — the group CN is syntactically valid but does not exist
  in Active Directory
- Returns 409 with code `RESOURCE_CONFLICT` if a mapping for the same
  (ad_group_cn, role) already exists
- Returns 503 with code `AD_UNAVAILABLE` if AD is unreachable or
  the connection timed out

Response (`201 Created`):
```json
{
  "data": {
    "id": "uuid",
    "ad_group_cn": "O SUSE Security",
    "role": "vulnerability_analyst",
    "created_by": { "id": "uuid", "username": "admin1", "full_name": "Admin User" },
    "created_at": "2026-05-06T12:00:00Z",
    "affected_users_count": 22
  }
}
```

A single AD group may have multiple mappings (one per role). For example,
group "O SUSE Security" can be mapped to both `admin` and
`vulnerability_analyst` simultaneously. Each mapping operates
independently — creating or deleting one does not affect the other.

Processing (all steps execute within a **single database transaction**):
1. Query AD live for members of the specified group. If AD is unreachable,
   the transaction is not committed — no RoleMapping or UserRole records
   are created (the global `AD_UNAVAILABLE` / 503 convention applies)
2. Create the `RoleMapping` record
3. Call `user_service.sync_role_mapping(role, ad_group_cn,
   member_user_ids, acting_user_id=acting_admin.id)` where
   `member_user_ids` is the set of User IDs matching the AD group
   members. The service creates `UserRole` records for each member
   and returns `(added_count, removed_count)` (see
   `docs/features/identity/user-service.md` for the full contract).
   For a new mapping, `removed_count` is always 0
4. Commit the transaction and return the created mapping.
   `affected_users_count` reflects the `added_count` returned by
   the service — only newly created `UserRole` records, not
   pre-existing ones (e.g., if a concurrent sync already applied the
   same mapping, those users are not counted)
5. Emit a structured audit log entry (INFO level, JSON format) containing:
   `admin_user_id`, `admin_username`, `ad_group_cn`, `role`,
   `affected_users_count`, `timestamp`. This log line follows the
   application's structured logging conventions (JSON log lines)

### Delete Role Mapping

```
DELETE /api/v1/admin/role-mappings/{id}
```

Admin only. Removes a role mapping. Identifies affected users from local
`UserRole` records matching the mapping's `ad_group_cn` and `role`.

Response (**200**):
```json
{
  "data": {
    "mapping": { "ad_group_cn": "O SUSE Security", "role": "vulnerability_analyst" },
    "affected_users_count": 22,
    "message": "Removed the 'vulnerability_analyst' role from 22 users."
  }
}
```

Processing (steps 2–4 execute within a single database transaction):
1. Look up the `RoleMapping` record by ID — return 404 if not found
2. Call `user_service.delete_role_mapping_roles(role, ad_group_cn,
   acting_user_id=acting_admin.id)`. The service removes all `UserRole`
   records tagged with this mapping's `(role, ad_group_cn)` and returns
   `affected_users_count`. If the acting admin would lose their only
   source of admin role, the service rejects with
   `SelfRoleRemovalError` — the endpoint maps this to 409 /
   `USER_SELF_ROLE_REMOVAL` (see
   `docs/features/identity/user-service.md` for the full contract)
3. Delete the `RoleMapping` record
4. Emit a structured audit log entry (INFO level, JSON format) containing:
   `admin_user_id`, `admin_username`, `ad_group_cn`, `role`,
   `revoked_users_count`, `timestamp`. This log line follows the
   application's structured logging conventions (JSON log lines)
5. Return 200 with the impact summary

This endpoint returns 200 with an impact summary instead of 204 because
the deletion has side effects (role revocation from affected users) that
the admin needs to confirm in the response.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `RESOURCE_NOT_FOUND` | Role mapping not found |
| 409 | `USER_SELF_ROLE_REMOVAL` | Deleting this mapping would remove the acting user's only source of admin role (via `user_service.delete_role_mapping_roles()`) |

Note: users who also have the same role via a different AD group mapping
or with `ad_group_cn = '_manual'` will retain the role.

## UI Requirements

### Users page and User detail page

The public users page and user detail page are defined in
`docs/features/identity/user-management.md` (UI section).

### Settings > Role Mappings page

Admin only. Accessible from the admin settings area.

Displays a table of all configured role mappings:
- AD Group CN
- Sentinel Role
- Created by (username)
- Created at
- Delete button

**Add Mapping flow**:
1. Admin enters the AD group CN (text input, or searchable dropdown if
   AD group listing is feasible)
2. Admin selects the Sentinel role from a dropdown
3. Admin clicks "Preview" → the system queries AD live and shows the list
   of users who would receive the role, along with a count
4. Admin reviews the list and clicks "Confirm" to create the mapping, or
   "Cancel" to abort
5. On confirmation, roles are applied immediately

**Delete Mapping flow**:
1. Admin clicks "Delete" on a mapping
2. The system shows a confirmation dialog with the count of users who
   will lose the role
3. Admin confirms or cancels

## Business Rules

1. **AD is the primary user source**: the User table is populated
   primarily from AD sync. Local user accounts (for development, bots,
   or environments without SSO) can be created exclusively via the CLI
   (`sentinel manage-user create`) — see
   `docs/features/identity/user-management.md`. There is no user creation through
   the UI or API
2. **Login is open**: any SUSE employee can authenticate via SSO (see
   `docs/features/identity/sso-authentication.md`). A user with no roles
   has the same access as an unauthenticated user (read-only on public
   data)
3. **Role assignment is hybrid**: roles can come from AD group mappings
   (automatic, managed by sync) or manual assignment by an admin.
   AD-derived roles cannot be removed manually — only by removing the
   user from the AD group or deleting the role mapping
4. **Override is additive only**: an admin can add manual roles to a user
   but cannot remove AD-derived roles. This prevents accidental
   revocation of roles that are managed centrally
5. **Deactivation cascades**: when an employee is deactivated in AD,
   the side effects are handled by `user_service.deactivate_user()` —
   see `docs/features/identity/user-service.md` for the full contract
    (ticket unassignment, API key revocation, TicketEvent creation)
6. **Admin self-removal protection**: an admin cannot remove their own
   Admin role via the API. The Admin role can be removed from a user only
   by a different admin, by the CLI, or by system actions (LDAP sync,
   fetchers). The LDAP sync does NOT enforce any minimum admin count — if
   the last active admin is deactivated in AD, the sync proceeds normally
   and logs a WARNING: `"Last active admin '{username}' has been
   deactivated by LDAP sync. Use 'sentinel manage-user update --username
   <user> --add-role admin' to restore admin access."` Recovery is always
   possible via CLI
7. **Manager chain**: the `manager_id` field enables traversal of the
   reporting chain by following User → manager → manager's manager, etc.
   This is resolved via the self-referencing FK on `user.id`
8. **Sync idempotency**: running the sync multiple times with the same AD
   data produces the same result. No duplicate records, no lost data
9. **Role mapping application is immediate**: when an admin creates or
   deletes a role mapping, the roles are applied/revoked immediately —
   not deferred to the next scheduled sync

## Security Considerations

- **LDAPS with TLS validation**: the connection to `pan.suse.de` uses
  LDAPS (port 636) with server certificate validation against the SUSE
  Trust Root CA (`certs/SUSE_Trust_Root.crt`). TLS is mandatory to
  protect the integrity of `MEMBEROF` responses used for role derivation
  — see the Security rationale section above for the full threat model
- LDAP queries use anonymous bind — no credentials are stored or
  transmitted. This is consistent with the current AD configuration at
  `pan.suse.de`. See the Anonymous Bind Risk Acceptance subsection below
  for the full risk analysis
### Anonymous Bind Risk Acceptance

Anonymous bind is a deliberate choice imposed by the SUSE AD infrastructure.
The OpenLDAP proxy at `pan.suse.de` allows anonymous bind and does not require
service credentials — there is no mechanism to authenticate with a dedicated
service account.

**Acknowledged risk**: any host with network access to `pan.suse.de:636` can
read the synced attributes (`sAMAccountName`, `cn`, `mail`, `manager`,
`EMPLOYEESTATUS`, `MEMBEROF`) without authentication.

**Existing mitigations**:

- **Network access control**: `pan.suse.de` is accessible only from the SUSE
  internal network. External access requires a VPN or equivalent
- **Mandatory TLS**: all connections use LDAPS (port 636) with server
  certificate validation, preventing eavesdropping and man-in-the-middle
  attacks on the network path
- **Low sensitivity of synced data**: the attributes retrieved are
  organizational directory data (names, emails, group memberships, employment
  status). No passwords, financial data, or other high-sensitivity PII is
  accessed or stored

**Future adaptation**: if the AD infrastructure is updated to require service
credentials in the future, the connection should adapt via environment
variables (`LDAP_BIND_DN`, `LDAP_BIND_PASSWORD`) without code changes. These
variables are not currently defined because anonymous bind does not use them.

- The `manager` field in AD is trusted as the authoritative source for
  the line manager relationship
- Role mapping creation validates that the AD group exists before saving
  (prevents typos from creating phantom mappings that could auto-assign
  roles if the group is later created in AD)
- Role mapping preview queries AD live, which means a temporary AD
  outage will prevent preview and mapping creation — this is acceptable
  since mapping management is a rare admin operation
- The `MEMBEROF` attribute is read but not persisted, minimizing the
  amount of AD data stored locally
- Employee personal data (name, email) is stored locally for operational
  purposes. No additional PII (phone, address, etc.) is imported
- **Audit logging for role mapping operations**: all role mapping CRUD
  operations (creation and deletion) are audit-logged via structured
  logging (JSON log lines at INFO level), capturing the admin identity,
  mapping details, affected user counts, and timestamps
- The CLI `manage-user` commands require shell access to the server,
  which is an appropriate security barrier for administrative operations.
  See `docs/features/identity/user-management.md`

### Admin Lockout Risk

If role mappings change such that the LDAP sync removes the `admin` role
from all users (e.g., the AD group mapped to `admin` is deleted or
emptied, or the role mapping is deleted), no user will have admin access
to the web UI or API admin endpoints.

The system does **NOT** enforce a minimum admin count. This is a
deliberate design choice — adding such enforcement would increase
complexity across multiple code paths (sync, role mapping deletion,
manual role removal) with diminishing returns, given that CLI recovery
is always available.

**Mitigations**:

- **CLI recovery**: the `sentinel manage-user update --username <user>
  --add-role admin` command is always available to restore admin roles.
  CLI commands are not subject to RBAC — they require shell access,
  which is an appropriate security barrier
- **Safety check (partial coverage)**: the pre-flight safety check in
  the sync algorithm (Level 2 — mass deactivation threshold) limits
  the risk of mass user deactivation but does not specifically cover
  role removal. Role mapping changes that revoke admin from all users
  will proceed without blocking
- **Admin self-removal protection**: an admin cannot remove their own
  Admin role via the API (Business Rule 6), which prevents accidental
  lockout during manual role management. However, this does not protect
  against LDAP sync removing the role based on AD group membership
  changes

A minimum admin count enforcement was evaluated and rejected for
simplicity — the CLI is a sufficient recovery path, and the scenario
requires a specific combination of AD group changes or role mapping
deletions that is unlikely to occur accidentally.

## Implementation Notes

- **DN parsing**: the `manager` attribute contains a full Distinguished
  Name (e.g., `CN=Bob Wilson,OU=User accounts,DC=corp,DC=suse,DC=com`).
  Implementations MUST use a standards-compliant DN parser (e.g.,
  `ldap3.utils.dn.parse_dn()`) to extract the CN component. Do NOT use
  naive string splitting — DNs may contain escaped commas within values
  (e.g., `CN=Wilson\, Bob`)
- **API endpoint timeouts**: the fetcher timeout (900s) is appropriate for
  the daily background sync (including retry attempts). However, API
  endpoints that query AD live
  (preview, mapping creation, mapping deletion) are synchronous HTTP
  requests and MUST set a short LDAP operation timeout (10–15 seconds).
  If AD is unreachable, these endpoints should return 503 with a clear
  error message rather than blocking the API worker indefinitely
- **AD group existence check**: the `POST /api/v1/admin/role-mappings`
  endpoint queries AD to verify the group CN exists before persisting
  the mapping. This reuses the same AD query infrastructure as the
  preview endpoint
- **LDAP client configuration**: when using the `ldap3` library, the
  `Server` object MUST be created with `get_info=NONE` (not
  `get_info=ALL`). The schema exposed by `pan.suse.de` is the OpenLDAP
  proxy's own schema (standard RFC object classes and attributes) — it
  does not include any AD-specific attributes (`sAMAccountName`,
  `objectGUID`, `MEMBEROF`, `EMPLOYEESTATUS`, etc.). Fetching schema
  from the proxy would provide a completely irrelevant attribute set and
  schema validation would reject every query that references AD
  attributes. Additionally, attribute key casing may vary between paged
  and non-paged search results; the sync code MUST use case-insensitive
  attribute key lookups or normalize keys after retrieval
- **Proxy cache (`pcache`) and "live" queries**: API endpoints described as
  querying AD "live" (`POST /role-mappings/preview`, `POST /role-mappings`
  group existence check) may not reflect very recent AD changes. This is a
  known limitation of the LDAP proxy infrastructure — see
  `docs/data-sources.md` (SUSE Active Directory section) for technical
  details. Implementations do NOT need to attempt cache bypass or surface
  this detail to administrators
- **Optional attributes**: `manager` is absent for approximately 1%
   of entries (top-level managers with no superior). `MEMBEROF` is
   absent for the majority of entries (~87%) since most employees are
   not members of relevant AD groups. `EMPLOYEESTATUS` may be absent
   for a small number of entries — these are classified as **skipped**
   during entry normalization (step 2b) and excluded from all
   subsequent processing; they remain in the complete result set for
   pre-flight checks (Level 1 uses the full set). `sAMAccountName`
   and `mail` may be absent for corrupted entries — also handled by
   entry normalization (step 2b). The sync code MUST handle all of
   these attributes as optional (use empty string or empty list as
   defaults)
- **LDAP operation-level timeouts**: the LDAP connection used by the
  sync fetcher MUST enforce two distinct timeouts, both configurable
  via custom settings:
  - **Connection timeout** (`ldap_connect_timeout` custom setting,
    default: **30** seconds, range: 5–120): maximum time to establish
    the TCP/TLS connection to `pan.suse.de`. Covers DNS resolution,
    TCP handshake, and TLS negotiation
  - **Operation timeout** (`ldap_operation_timeout` custom setting,
    default: **120** seconds, range: 30–600): maximum time for a
    single LDAP search operation to complete (including paged result
    fetching). Covers server-side processing delays and network
    stalls during data transfer
  - These timeouts are distinct from and subordinate to the Celery task
    timeout (900s), which serves as the last-resort safety net. The LDAP
    timeouts provide faster, more specific failure detection
  - On connection timeout: the fetcher fails with ERROR:
    `"LDAP connection timeout after {n}s — unable to establish
    connection to {uri}. Verify network connectivity and DNS resolution."`
  - On operation timeout: the fetcher fails with ERROR:
    `"LDAP operation timeout after {n}s — search operation did not
    complete. This may indicate server overload or network issues."`

## Cross-references

- `docs/api-spec.md` — global API conventions (envelope format, error codes,
  pagination, shared 422 responses)
