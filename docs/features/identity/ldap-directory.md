# LDAP Directory Integration

## Purpose

Synchronize SUSE employee data from Active Directory into Sentinel to enable
user pre-provisioning, autocomplete search, package bugowner enrichment,
automatic role assignment from AD group membership, and manager-based
ticket escalation when employees leave the organization.

The LDAP sync fetcher is the **sole authority** for identity data of LDAP
users (`email`, `full_name`, `ldap_dn`, `manager_uid`, `ldap_synced_at`).
These fields cannot be modified manually via the API or CLI. See LDAP User
Data Ownership in `docs/features/identity/user-lifecycle.md` for the
service-layer enforcement rules.

## Data Source

Sentinel uses the SUSE Active Directory instance at `pan.suse.de` as the
single source of truth for employee identity data. See
`docs/data-sources.md` for connection details.

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

### Attributes consumed

| AD Attribute      | Sentinel Field       | Description                          |
|-------------------|-------------------|--------------------------------------|
| `sAMAccountName`  | `ldap_uid`        | Username (e.g., `ggabrielli`)        |
| `cn`              | `full_name`       | Full display name                    |
| `mail`            | `email`           | Primary email (`.com`)               |
| `manager`         | `manager_uid`     | DN of the direct line manager        |
| `EMPLOYEESTATUS`  | `active`          | Employee status (`Active` or other)  |
| `distinguishedName` | `ldap_dn`       | Full DN in Active Directory          |

The `MEMBEROF` attribute is read during sync to apply role mappings but
is not persisted in the database.

## Data Model

### Changes to User table

The existing `User` table is extended with LDAP-specific fields. All
active employees from AD (~913 records) are synced into this table.

| Column         | Type        | Constraints            | Description                        |
|----------------|-------------|------------------------|------------------------------------|
| ldap_uid       | VARCHAR     | UNIQUE, nullable       | AD `sAMAccountName`. NULL for non-LDAP users (e.g., future service accounts) |
| ldap_dn        | VARCHAR     | nullable               | Full AD distinguished name         |
| manager_uid    | VARCHAR     | nullable               | `ldap_uid` of the direct line manager (self-referencing via `User.ldap_uid`) |
| ldap_synced_at | TIMESTAMP   | nullable               | When this record was last synced from AD |

The `username` field is populated from `sAMAccountName`, `email` from
`mail`, and `full_name` from `cn`. The `active` field is set based on
`EMPLOYEESTATUS`: `true` when the value is `Active`, `false` otherwise.

### Changes to UserRole table

The existing `UserRole` table is extended with an `ad_group_cn` column
that tracks the origin of each role assignment, and an `assigned_by`
column that records which user performed the assignment (NULL for system
actions). The previous `source` ENUM column is removed — it is now
derivable from `ad_group_cn` (`_manual` = manual, anything else = AD).

| Column       | Type        | Constraints                   | Description                        |
|--------------|-------------|-------------------------------|------------------------------------|
| ad_group_cn  | VARCHAR     | NOT NULL, DEFAULT `'_manual'` | AD group CN that granted this role, or `_manual` for manual assignments |
| assigned_by  | UUID        | FK(user.id), nullable         | User who assigned the role. NULL for system actions (LDAP sync, CLI) |

Roles with `ad_group_cn != '_manual'` cannot be removed by an admin
through the UI or API. They are managed exclusively by the sync process
based on AD group membership and the configured role mappings. Roles
with `ad_group_cn = '_manual'` can be added or removed by an admin at
any time.

**Unique constraint**: (user_id, role, ad_group_cn) — allows a user to
hold the same role from multiple AD groups simultaneously (one record
per group), plus an optional manual assignment.

### New table: RoleMapping

Stores the mapping rules between AD groups and Sentinel roles, configured
by admins.

| Column       | Type        | Constraints                  | Description                        |
|--------------|-------------|------------------------------|------------------------------------|
| id           | UUID        | PK                           | Internal identifier                |
| ad_group_cn  | VARCHAR     | NOT NULL                     | AD group common name (e.g., `O SUSE Security`) |
| role         | ENUM        | NOT NULL                     | Sentinel role to assign: `Admin` or `Vulnerability Analyst` |
| created_by   | UUID        | FK(user.id), NOT NULL        | Admin who created this mapping     |
| created_at   | TIMESTAMP   | NOT NULL, DEFAULT            | Record creation timestamp          |

**Unique constraint**: (ad_group_cn, role)

## Fetcher

### `sync_ldap_directory`

A `BaseFetcher` subclass registered in the fetcher dashboard.

- **Name**: `sync_ldap_directory`
- **Description**: Syncs SUSE employee data from Active Directory
- **Default schedule**: daily at 04:00 UTC

#### Sync algorithm

1. **Query AD**: fetch all entries under
   `OU=User accounts,DC=corp,DC=suse,DC=com` with attributes
   `sAMAccountName`, `cn`, `mail`, `manager`, `EMPLOYEESTATUS`,
   `distinguishedName`, `MEMBEROF`
2. **Safety check** (evaluated before any `active` field modification):
   - Compute the **deactivation candidate set**: existing users with
     `ldap_uid IS NOT NULL AND active = true` that are either absent
     from the AD results or have `EMPLOYEESTATUS != Active`
   - If the AD query returned **zero results**, abort the entire sync
     immediately with status `failure` and log ERROR:
     `"AD returned zero entries. Aborting sync to prevent mass
     deactivation. Verify AD connectivity."`
   - If the deactivation candidate count exceeds
     `LDAP_SYNC_MAX_DEACTIVATIONS` (env var, default: **20**), **freeze
     all `active` field changes** for this run (both deactivations and
     reactivations):
     - Log ERROR: `"LDAP sync would deactivate {n} users (threshold:
       {max}). All active-status changes frozen for this run. Review
       manually and re-run with increased threshold if intentional."`
     - Mark the run as `partial`
     - Steps 3–6 proceed normally but **skip the `active` field**
     - Step 7 is skipped entirely (no `active` transitions occurred)
   - If the safety check passes (candidate count within threshold),
     `active` field updates are enabled for this run
3. **Upsert users**: for each AD entry:
   - If a `User` record with matching `ldap_uid` exists, update
     `full_name`, `email`, `ldap_dn`, `ldap_synced_at` via
     `user_service.update_user()`. The `active` field is NOT modified
     in this step — deactivations and reactivations are handled in
     steps 6 and 7 respectively
   - If no matching record exists, attempt to create a new `User` via
     `user_service.create_user()` with `username = sAMAccountName`,
     `email = mail`, `full_name = cn`, `ldap_uid = sAMAccountName`,
     `ldap_dn = distinguishedName`,
     `active = (EMPLOYEESTATUS == "Active")`, `acting_user_id = None`.
     Note: new user creation always sets `active` regardless of the
     safety check — the check protects existing users only
   - If creation raises `UserConflictError` (collision with an existing
     local user that has `ldap_uid = NULL`), log WARNING:
     `"Cannot create LDAP user '{sAMAccountName}': {field} conflicts
     with existing local user '{existing_username}'."`, call
     `record_failed()`, and skip this entry. The admin must resolve the
     conflict manually (e.g., rename or delete the local user) and
     re-run the sync. The sync continues processing remaining entries
   - **Only if the safety check passed**: identify existing LDAP users
     (`ldap_uid IS NOT NULL`) that should be deactivated (absent from AD
     results or `EMPLOYEESTATUS != Active` while currently `active = true`)
     and add them to the `newly_deactivated` list. Also identify users
     with `active = false` that now have `EMPLOYEESTATUS == Active` and
     add them to the `newly_reactivated` list. No `active` field writes
     happen in this step
4. **Resolve managers**: for each user, extract the `cn` from the
   `manager` DN (e.g., `cn=Stoyan Manolov,...` → look up the User with
   that `cn` or query AD for the `sAMAccountName` of that DN) and set
   `manager_uid` to the corresponding `ldap_uid`. If the manager is not
   found in the User table, set `manager_uid = NULL`
5. **Apply role mappings** (incremental per mapping): for each
   `RoleMapping(ad_group_cn, role)` in the database:
   - Identify all users whose `MEMBEROF` (from the AD data already in
     memory) includes this mapping's `ad_group_cn`
   - **Add**: for each user in the AD group who does not have a
     `UserRole(user_id, role, ad_group_cn)` record in the DB, create
     one with `assigned_by = NULL`
   - **Remove**: for each `UserRole` record in the DB with this
     specific `(role, ad_group_cn)` whose `user_id` is no longer in
     the AD group, delete the record
   - Each mapping operates exclusively on records tagged with its own
     `ad_group_cn`. Manual roles (`_manual`) and records from other
     mappings are never touched. Processing order is irrelevant
   - For the full semantics of how AD-derived and manual role
     assignments coexist independently, see `docs/features/identity/rbac.md`
     (Role Origins and Coexistence)
6. **Deactivation side effects**: for each user in the
   `newly_deactivated` list (identified in step 3), call
   `user_service.deactivate_user()` with
   `reason = "employee deactivated in LDAP"` and
   `acting_user_id = None`. The service sets `active = false` and
   executes all side effects atomically. See
    `docs/features/identity/user-lifecycle.md` for the full contract (ticket
    unassignment, API key revocation, TicketEvent creation). This step
   is skipped entirely when the safety check froze `active` changes
   (the `newly_deactivated` list is empty)
7. **Reactivation**: for each user in the `newly_reactivated` list
   (identified in step 3), call `user_service.reactivate_user()` with
   `acting_user_id = None`. See `docs/features/identity/user-lifecycle.md` for
    reactivation semantics (previously unassigned tickets and API keys
   are NOT restored). This step is skipped entirely when the safety
   check froze `active` changes
8. **Metrics**: report `record_created()` for new users,
   `record_updated()` for updated users, `record_failed()` for entries
   that failed processing

#### Manager resolution

The `manager` attribute in AD contains a full DN (e.g.,
`cn=Stoyan Manolov,ou=User accounts,dc=corp,dc=suse,dc=com`). During
sync, the fetcher resolves this to a `ldap_uid` by:

1. Looking up the DN in the current sync batch (preferred, avoids extra
   AD queries)
2. If not found in the batch, querying AD for the `sAMAccountName` of
   that DN

The `manager_uid` field stores only the `ldap_uid` string (not a foreign
key), because the manager might not be in the User table (e.g., a senior
executive who has not been synced yet). The relationship is resolved at
query time via `User.ldap_uid`.

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
1. sentinel fetcher run sync_ldap_directory                        # populate User table (~913 records)
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

Extended with autocomplete support. Public endpoint (read-only).

Query parameters:
- `search` (string, optional): searches across `username`, `email`, and
  `full_name`. Minimum 2 characters. Supports partial matching
- `active` (boolean, optional): filter by active status
- `role` (enum, optional): filter by role (`admin`, `vulnerability_analyst`)
- `has_role` (boolean, optional): `true` to return only users with at
  least one role, `false` for users with no roles
- Standard pagination (`page`, `per_page`) and sorting (`sort_by`,
  `sort_order`)

Response uses the standard paginated envelope (`data` array + `meta`
object). Each user object follows the same schema as
`GET /api/v1/users/{user}` (see User detail below).

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 422 | `VALIDATION_ERROR` | `search` parameter shorter than 2 characters |

### User detail

```
GET /api/v1/users/{user}
```

Returns full user profile. Public endpoint (read-only). Response uses
the standard single-resource envelope:

```json
{
  "data": {
    "id": "uuid",
    "username": "string",
    "email": "string",
    "full_name": "string",
    "active": true,
    "ldap_uid": "string | null",
    "manager": {
      "id": "uuid",
      "username": "string",
      "full_name": "string",
      "email": "string"
    } | null,
    "roles": [
      {
        "role": "admin",
        "ad_group_cn": "O SUSE Security",
        "assigned_by": "uuid | null",
        "created_at": "ISO8601"
      }
    ],
    "created_at": "ISO8601",
    "updated_at": "ISO8601"
  }
}
```

Field notes:
- `manager`: resolved manager object (from AD `manager` DN) or `null`
- `roles`: array of all roles from both AD group mappings and manual
  assignments. `ad_group_cn` is `'_manual'` for manually assigned roles

Public endpoint (read-only).

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `USER_NOT_FOUND` | No user found matching the given UUID or username |

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
      { "id": "uuid", "username": "ggabrielli", "full_name": "Gianluca Gabrielli", "email": "..." },
      { "id": "uuid", "username": "jsegitz", "full_name": "Johannes Segitz", "email": "..." }
    ],
    "affected_count": 22,
    "unknown_users": ["newemployee"]
  }
}
```

The `unknown_users` field lists AD usernames found in the group but not
yet present in the User table (e.g., employees hired after the last
sync). These users will receive the role at the next sync.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 422 | `VALIDATION_ERROR` | Invalid request body (missing or empty `ad_group_cn`, unrecognized `role`) |
| 503 | `AD_UNAVAILABLE` | AD is unreachable or the connection timed out (10–15 s timeout) |

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
    "created_at": "2026-05-06T12:00:00Z",
    "affected_users_count": 22
  }
}
```

A single AD group may have multiple mappings (one per role). For example,
group "O SUSE Security" can be mapped to both `admin` and
`vulnerability_analyst` simultaneously. Each mapping operates
independently — creating or deleting one does not affect the other.

Processing:
1. Create the `RoleMapping` record
2. Query AD live for members of the specified group
3. For each member found in the User table, create a `UserRole` with
   `ad_group_cn` set to the mapping's group CN and `assigned_by = NULL`
   (if not already present for that user/role/group combination)
4. Return the created mapping with the count of affected users

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

Processing:
1. Look up the `RoleMapping` record by ID — return 404 if not found
2. Count `UserRole` records where `ad_group_cn` matches the mapping's
   group CN and `role` matches the mapping's role (this is the
   `affected_users_count` in the response)
3. Remove those `UserRole` records
4. Delete the `RoleMapping` record
5. Return 200 with the impact summary

This endpoint returns 200 with an impact summary instead of 204 because
the deletion has side effects (role revocation from affected users) that
the admin needs to confirm in the response.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `RESOURCE_NOT_FOUND` | Role mapping not found |

Note: users who also have the same role via a different AD group mapping
or with `ad_group_cn = '_manual'` will retain the role.

## UI Requirements

### Users page

Accessible to all users (public, read-only). Displays a searchable,
sortable table of all users.

Columns:
- Full name
- Username (ldap_uid)
- Email
- Roles (badges showing role name and origin icon: lock for AD-derived,
  pencil for manual)
- Active status
- Manager name

Features:
- Search field with autocomplete (min 2 characters, searches name/email/
  username)
- Filter by role, active status
- Click row to navigate to user detail page

### User detail page

Accessible to all users (public, read-only for non-admins). Shows:

- **Profile section**: full name, username, email, active status, manager
  (linked to their profile), LDAP sync timestamp
- **Roles section** (editable by Admin only):
  - AD-derived roles: displayed with a lock icon and the source group
    name (e.g., `Vulnerability Analyst 🔒 from "O SUSE Security"`). Not
    removable
  - Manual roles: displayed with a remove button. Removable by admin
  - "Add Role" button: dropdown to add a manual role
- **Assigned tickets section**: list of tickets currently assigned to
  this user

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
   see `docs/features/identity/user-lifecycle.md` for the full contract
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
7. **Manager chain**: the `manager_uid` field enables traversal of the
   reporting chain by following User → manager → manager's manager, etc.
   This is resolved at query time, not pre-computed
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
  `pan.suse.de`
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
- The CLI `manage-user` commands require shell access to the server,
  which is an appropriate security barrier for administrative operations.
  See `docs/features/identity/user-management.md`

## Implementation Notes

- **DN parsing**: the `manager` attribute contains a full Distinguished
  Name (e.g., `CN=Mario Rossi,OU=User accounts,DC=corp,DC=suse,DC=com`).
  Implementations MUST use a standards-compliant DN parser (e.g.,
  `ldap3.utils.dn.parse_dn()`) to extract the CN component. Do NOT use
  naive string splitting — DNs may contain escaped commas within values
  (e.g., `CN=Rossi\, Mario`)
- **API endpoint timeouts**: the fetcher timeout (300s) is appropriate for
  the daily background sync. However, API endpoints that query AD live
  (preview, mapping creation, mapping deletion) are synchronous HTTP
  requests and MUST set a short LDAP operation timeout (10–15 seconds).
  If AD is unreachable, these endpoints should return 503 with a clear
  error message rather than blocking the API worker indefinitely
- **AD group existence check**: the `POST /api/v1/admin/role-mappings`
  endpoint queries AD to verify the group CN exists before persisting
  the mapping. This reuses the same AD query infrastructure as the
  preview endpoint
