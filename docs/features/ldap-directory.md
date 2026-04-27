# LDAP Directory Integration

## Purpose

Synchronize SUSE employee data from Active Directory into STAMP to enable
user pre-provisioning, autocomplete search, package bugowner enrichment,
automatic role assignment from AD group membership, and manager-based
ticket escalation when employees leave the organization.

## Data Source

STAMP uses the SUSE Active Directory instance at `pan.suse.de` as the
single source of truth for employee identity data. See
`docs/data-sources.md` for connection details.

- **Server**: `ldap://pan.suse.de`
- **Base DN**: `OU=User accounts,DC=corp,DC=suse,DC=com`
- **Authentication**: anonymous bind (no credentials required)
- **Protocol**: LDAP (port 389, plaintext — no TLS/STARTTLS). This is an
  explicit design decision: the connection uses anonymous bind (no
  credentials transmitted) and traverses the SUSE internal network only.
  The data exchanged (employee names, emails, group memberships) is
  already widely known within the organization. The security trade-off
  is accepted.

### Attributes consumed

| AD Attribute      | STAMP Field       | Description                          |
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

A `source` field is added to track the origin of each role assignment.

| Column | Type | Constraints | Description                                      |
|--------|------|-------------|--------------------------------------------------|
| source | ENUM | NOT NULL    | `ad_group` (from AD group mapping) or `manual` (assigned by admin) |

Roles with `source = ad_group` cannot be removed by an admin through the
UI or API. They are managed exclusively by the sync process based on AD
group membership and the configured role mappings. Roles with
`source = manual` can be added or removed by an admin at any time.

### New table: RoleMapping

Stores the mapping rules between AD groups and STAMP roles, configured
by admins.

| Column       | Type        | Constraints                  | Description                        |
|--------------|-------------|------------------------------|------------------------------------|
| id           | UUID        | PK                           | Internal identifier                |
| ad_group_cn  | VARCHAR     | NOT NULL                     | AD group common name (e.g., `O SUSE Security`) |
| role         | ENUM        | NOT NULL                     | STAMP role to assign: `Admin` or `Vulnerability Analyst` |
| created_by   | UUID        | FK(user.id), NOT NULL        | Admin who created this mapping     |
| created_at   | TIMESTAMP   | NOT NULL, DEFAULT            | Record creation timestamp          |

**Unique constraint**: (ad_group_cn, role)

## Fetcher

### `sync_ldap_directory`

A `BaseFetcher` subclass registered in the fetcher dashboard.

- **Name**: `sync_ldap_directory`
- **Description**: Syncs SUSE employee data from Active Directory
- **Default schedule**: daily at 04:00 UTC
- **Timeout**: 300 seconds (5 minutes)

#### Sync algorithm

1. **Query AD**: fetch all entries under
   `OU=User accounts,DC=corp,DC=suse,DC=com` with attributes
   `sAMAccountName`, `cn`, `mail`, `manager`, `EMPLOYEESTATUS`,
   `distinguishedName`, `MEMBEROF`
2. **Upsert users**: for each AD entry:
   - If a `User` record with matching `ldap_uid` exists, update
     `full_name`, `email`, `ldap_dn`, and `ldap_synced_at`
   - If no matching record exists, create a new `User` with
     `username = sAMAccountName`, `email = mail`, `full_name = cn`,
     `ldap_uid = sAMAccountName`, `ldap_dn = distinguishedName`,
     `active = (EMPLOYEESTATUS == "Active")`, `ldap_synced_at = now()`
3. **Resolve managers**: for each user, extract the `cn` from the
   `manager` DN (e.g., `cn=Stoyan Manolov,...` → look up the User with
   that `cn` or query AD for the `sAMAccountName` of that DN) and set
   `manager_uid` to the corresponding `ldap_uid`. If the manager is not
   found in the User table, set `manager_uid = NULL`
4. **Apply role mappings**: for each `RoleMapping` in the database:
   - Identify all users whose `MEMBEROF` includes the mapped
     `ad_group_cn`
   - For each such user, ensure a `UserRole` record exists with the
     mapped role and `source = ad_group`
   - For users who previously had a `UserRole` with `source = ad_group`
     for this mapping but are no longer in the AD group, remove the
     `UserRole` record
5. **Handle deactivations**: for each `User` with `ldap_uid IS NOT NULL`
   that is not present in the AD results or whose `EMPLOYEESTATUS` is not
   `Active`:
   - Set `User.active = false`
   - Revoke all API keys belonging to this user (see
     `docs/features/sso-authentication.md`, planned)
   - Reassign open tickets: for each ticket where `assignee_id` points
     to the deactivated user, reassign to the user identified by
     `manager_uid`. If the manager is not active, has no compatible role
     (Vulnerability Analyst), or `manager_uid` is NULL, set
     `assignee_id = NULL` (unassigned). Create a `TicketEvent` of type
     `assignment` for each reassignment with `user_id = NULL` (system
     action) and `comment` describing the reason (e.g.,
     `"Reassigned from {old_assignee} to {manager}: employee deactivated
     in LDAP"`)
6. **Metrics**: report `record_created()` for new users,
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

## CLI Commands

### `stamp ldap-sync`

Triggers an immediate LDAP directory sync, bypassing the scheduler. Used
for initial population after deployment and for troubleshooting.

```
stamp ldap-sync
```

Output: summary of created/updated/deactivated users and role changes.

### Post-deployment bootstrap sequence

```
1. stamp ldap-sync                                              # populate User table (~913 records)
2. stamp manage-user update --username admin1 --add-role admin  # assign Admin role to first admin
```

The `manage-user` command is documented in
`docs/features/local-user-management.md`. In this bootstrap context, the
user already exists (created by the LDAP sync in step 1), and
`manage-user update` adds the Admin role with `source = manual`.

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

Response includes `roles` array with `source` field for each role.

### User detail

```
GET /api/v1/users/{id}
```

Returns full user profile including:
- User fields (`username`, `email`, `full_name`, `active`, `ldap_uid`,
  `manager`)
- `manager`: resolved manager object (`id`, `username`, `full_name`,
  `email`) or `null`
- `roles`: array of `{ role, source, created_at }`

Public endpoint (read-only).

### User role management

```
PUT /api/v1/users/{id}/roles
```

Admin only. Add or remove manual roles for a user.

Request body:
```json
{
  "add": ["admin"],
  "remove": ["vulnerability_analyst"]
}
```

Validation rules:
- Cannot remove roles with `source = ad_group` — returns 400 with
  `"Cannot remove AD-derived role '{role}'. This role is managed by the
  AD group '{ad_group_cn}'."`
- Cannot remove the last Admin role in the system — returns 409 with
  `"Cannot remove the last Admin role. At least one admin must exist."`
- Adding a role that the user already has (regardless of source) is a
  no-op (idempotent)
- Creates a `UserRole` record with `source = manual` for each added role
- Returns 200 with updated user profile including all roles

### Role Mapping management

```
GET /api/v1/admin/role-mappings
```

Admin only. Returns all configured role mappings.

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
  "ad_group_cn": "O SUSE Security",
  "role": "vulnerability_analyst",
  "affected_users": [
    { "id": "uuid", "username": "ggabrielli", "full_name": "Gianluca Gabrielli", "email": "..." },
    { "id": "uuid", "username": "jsegitz", "full_name": "Johannes Segitz", "email": "..." }
  ],
  "affected_count": 22,
  "unknown_users": ["newemployee"]
}
```

The `unknown_users` field lists AD usernames found in the group but not
yet present in the User table (e.g., employees hired after the last
sync). These users will receive the role at the next sync.

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
- Returns 422 if the AD group does not exist (queries AD live to verify)
- Returns 409 if a mapping for the same (ad_group_cn, role) already exists

Processing:
1. Create the `RoleMapping` record
2. Query AD live for members of the specified group
3. For each member found in the User table, create a `UserRole` with
   `source = ad_group` (if not already present)
4. Return the created mapping with the count of affected users

```
DELETE /api/v1/admin/role-mappings/{id}
```

Admin only. Removes a role mapping. Before deletion, queries AD live to
determine affected users.

Response (confirmation data):
```json
{
  "mapping": { "ad_group_cn": "O SUSE Security", "role": "vulnerability_analyst" },
  "affected_users_count": 22,
  "message": "Removing this mapping will revoke the 'vulnerability_analyst' role from 22 users."
}
```

Processing:
1. Remove all `UserRole` records with `source = ad_group` that were
   created by this mapping (matching role and users who are members of
   the AD group)
2. Delete the `RoleMapping` record
3. Return 200 with the summary

Note: users who also have the same role with `source = manual` will
retain the role.

## UI Requirements

### Users page

Accessible to all users (public, read-only). Displays a searchable,
sortable table of all users.

Columns:
- Full name
- Username (ldap_uid)
- Email
- Roles (badges showing role name and source icon: lock for AD, pencil
  for manual)
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
- STAMP Role
- Created by (username)
- Created at
- Delete button

**Add Mapping flow**:
1. Admin enters the AD group CN (text input, or searchable dropdown if
   AD group listing is feasible)
2. Admin selects the STAMP role from a dropdown
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

1. **All STAMP users are SUSE employees**: the User table is populated
   exclusively from AD sync. There is no manual user creation through the
   UI or API. In environments where AD is not reachable, local user
   accounts can be created via the CLI — see
   `docs/features/local-user-management.md`
2. **Login is open**: any SUSE employee can authenticate via SSO (see
   future `docs/features/sso-authentication.md`). A user with no roles
   has the same access as an unauthenticated user (read-only on public
   data)
3. **Role assignment is hybrid**: roles can come from AD group mappings
   (automatic, managed by sync) or manual assignment by an admin.
   AD-derived roles cannot be removed manually — only by removing the
   user from the AD group or deleting the role mapping
4. **Override is additive only**: an admin can add manual roles to a user
   but cannot remove AD-derived roles. This prevents accidental
   revocation of roles that are managed centrally
5. **Deactivation cascades**: when an employee is deactivated in AD:
   - The STAMP account is marked inactive
   - API keys are revoked
   - Assigned tickets are reassigned to the line manager (if the manager
     is active and has the VA role), otherwise set to unassigned
   - A TicketEvent is created for each reassignment
6. **At least one admin**: the system must always have at least one
   active user with the Admin role. The API and CLI enforce this
   constraint
7. **Manager chain**: the `manager_uid` field enables traversal of the
   reporting chain by following User → manager → manager's manager, etc.
   This is resolved at query time, not pre-computed
8. **Sync idempotency**: running the sync multiple times with the same AD
   data produces the same result. No duplicate records, no lost data
9. **Role mapping application is immediate**: when an admin creates or
   deletes a role mapping, the roles are applied/revoked immediately —
   not deferred to the next scheduled sync

## Security Considerations

- **Plaintext LDAP (whitelisted)**: the connection to `pan.suse.de:389`
  uses plaintext LDAP without TLS or STARTTLS. This is an explicit
  project decision, not an oversight. Rationale: anonymous bind (no
  credentials transmitted), internal SUSE network only, and the data
  exchanged (employee names, emails, group memberships) is already
  widely known within the organization. Security reviewers should treat
  this as a whitelisted exception.
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
  See `docs/features/local-user-management.md`

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
