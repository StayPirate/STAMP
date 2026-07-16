# Plan: Remove LDAP/AD Integration and Generalize Identity Model

## Status

**Draft** — pending review before execution.

## Context

The LDAP endpoint at `pan.suse.de` (OpenLDAP proxy to SUSE Active
Directory) is being decommissioned. The replacement provisioning
mechanism will be SCIM push from SUSEID (Authentik), with hourly full
sync and near-real-time event delivery. The SCIM specification is not
yet finalized (authentication, scope, retry semantics, and group naming
are still in negotiation with the infrastructure team).

Sentinel is in the specification phase — no implementation code or
database exists. The project will proceed to implementation using only
local users initially. External user provisioning (via SCIM or
equivalent) will be specified and implemented later.

This plan removes all LDAP-specific content from the specifications,
generalizes the identity data model to be provider-neutral (supporting
any future external provisioning mechanism), and creates a new deferred
specification that captures the role-mapping management endpoints and
the provisioning context for the future SCIM work.

## Decisions (consolidated)

| Decision | Choice |
|----------|--------|
| Identity scaffolding | Generalize/rename to provider-neutral terms |
| SSO authentication | Keep spec, mark `enabled:false` in tracking, generalize references |
| `ad-integration.md` | Delete; create `identity-provisioning.md` (disabled) with deferred content + SCIM context |
| `User.source` value | `"external"` \| `"local"` |
| Fetcher `source_type` enum `ldap` | Remove (SCIM is push-based, not a fetcher) |
| Role-mapping endpoints | Move to new `identity-provisioning.md` (disabled) |

## Naming Scheme

All AD-specific names are replaced with provider-neutral equivalents:

| Current (AD-specific) | New (provider-neutral) | Notes |
|---|---|---|
| `User.ad_object_guid` | `User.external_id` | Stable external identifier (UUID from provider) |
| `User.ad_synced_at` | `User.synced_at` | Last sync timestamp from external provider |
| `User.manager_id` | `User.manager_id` | Unchanged (already neutral) |
| `User.source = "ad"` | `User.source = "external"` | Derived field in API responses |
| `UserRole.ad_group_cn` | `UserRole.group_name` | Group name that granted this role, or `_manual` |
| `RoleMapping.ad_group_cn` | `RoleMapping.group_name` | External group name mapped to a role |
| `ADUserFieldReadOnlyError` | `ExternalUserFieldReadOnlyError` | — |
| `ADUserStatusReadOnlyError` | `ExternalUserStatusReadOnlyError` | — |
| `ADUserPasswordError` | `ExternalUserPasswordError` | — |
| `ADDerivedRoleError` | `DerivedRoleError` | Drops "AD" — applies to any external derivation |
| `USER_AD_STATUS_READONLY` | `USER_EXTERNAL_STATUS_READONLY` | Error code |
| `USER_AD_ROLE_PROTECTED` | `USER_EXTERNAL_ROLE_PROTECTED` | Error code |
| `USER_AD_FIELD_READONLY` | `USER_EXTERNAL_FIELD_READONLY` | Error code |
| `USER_AD_PASSWORD_FORBIDDEN` | `USER_EXTERNAL_PASSWORD_FORBIDDEN` | Error code |
| `USER_AD_LOCKOUT` | removed | Orphaned code — no consumer in any feature spec (see Finding 2) |
| `AD_UNAVAILABLE` | `PROVISIONING_UNAVAILABLE` | Resource-unavailable code (deferred) |
| `ROLE_MAPPING_GROUP_NOT_FOUND` | unchanged | Still valid for any external group |
| `ROLE_MAPPING_INVALID_GROUP_CN` | `ROLE_MAPPING_INVALID_GROUP_NAME` | Drops "CN" (LDAP term) |
| `chk_user_auth_exclusive` | unchanged | Logic: `external_id XOR password_hash` |
| Audit actor "AD sync" | "external sync" | In IdentityAuditEvent detail/prose |
| `--type ad` (CLI) | `--type external` | CLI filter parameter |
| `sync_ldap_directory` fetcher | removed | No replacement now; future SCIM reconciliation TBD |
| Source_type enum `ldap` | removed | Fetcher source-component enum |

### Terminology convention update

The current `docs/conventions.md` section "Active Directory / LDAP / SSO
Terminology" is replaced with a new "External Identity / SSO
Terminology" section:

| Term | Scope | Usage |
|------|-------|-------|
| **External** | Data origin | Prefix for columns, error classes, error codes, and CLI/API values that identify data originating from an external identity provider. Examples: `external_id`, `synced_at`, `ExternalUserStatusReadOnlyError`, `USER_EXTERNAL_STATUS_READONLY`, `--type external`, `"source": "external"` |
| **SSO** | Authentication method | Used only for the browser-based single sign-on flow (OIDC/OAuth2). Never as a user type — external users authenticate via SSO, but their identity source is the external provider |
| **SCIM** | Provisioning protocol (future) | Used only when referring to the SCIM 2.0 protocol specifically (RFC 7642-7644). The generic term for the provisioning capability is "external provisioning" |

Rules:
- A user whose `external_id IS NOT NULL` is an "external user" (not
  "LDAP user", "SSO user", or "directory user")
- A user whose `external_id IS NULL` is a "local user"
- The `source` field in API responses returns `"external"` or `"local"`

---

## Execution Plan

### Step 1 — Create `docs/features/identity/identity-provisioning.md`

**Action**: create new file.

**Content structure**:

```markdown
# External Identity Provisioning

## Status

**Deferred**. This specification is not yet active. External user
provisioning will be implemented after the local-only phase, once the
SCIM integration with SUSEID is finalized.

## Purpose

Define the external identity provisioning mechanism: how Sentinel
receives user identity data from an external provider, maps external
group memberships to Sentinel roles, and manages the lifecycle of
externally-provisioned users.

This specification owns:
- The provisioning mechanism (push-based, future SCIM)
- The role-mapping management API endpoints
- The bootstrap sequence for external provisioning
- The reconciliation strategy

## Background

[Sanitized summary of the LDAP deprecation and SCIM negotiation.
Include: what data is needed (uuid, uid, cn, mail, active, managerUid,
groups), that SCIM full sync runs hourly, that DELETE is sent for
removed users, that PATCH active:false for deactivated users, that
Users sync before Groups, that Service Provider assigns the resource
ID, that static bearer token is used for auth, and the open questions
remaining.]

## Provider Data Requirements

| Sentinel field | External provider field | Notes |
|---|---|---|
| `external_id` | Provider's stable UUID | Immutable matching key |
| `username` | Provider's username/uid | Updated on every sync if changed |
| `full_name` | Display name (cn) | — |
| `email` | Primary email | Normalized to lowercase |
| `active` | Active/inactive status | Drives deactivation side effects |
| `manager_id` | Manager's username/uid | Resolved to User FK |
| Group memberships | Group names/displayNames | Used for role mapping derivation |

## Role Mapping Management Endpoints (Deferred)

[Generalized from ad-integration.md, with these changes:
- "AD group CN" → "external group name"
- "Queries AD live" → "queries the external provider" (mechanism TBD)
- Validation: group_name characters valid for the provider
- Error PROVISIONING_UNAVAILABLE replaces AD_UNAVAILABLE
- All other semantics (immediate application, transaction model,
  self-admin guard, audit events) remain identical]

### List Role Mappings (deferred)
GET /api/v1/admin/role-mappings
[Same contract as current, with group_name replacing ad_group_cn]

### Preview Role Mapping (deferred)
POST /api/v1/admin/role-mappings/preview
[Requires live query to external provider — mechanism TBD with SCIM]

### Create Role Mapping (deferred)
POST /api/v1/admin/role-mappings
[Same contract, generalized; group existence check mechanism TBD]

### Delete Role Mapping (deferred)
DELETE /api/v1/admin/role-mappings/{id}
[Same contract, no external query needed — uses local UserRole records]

## Provisioning Mechanism (Placeholder)

No provisioning mechanism is active in the current phase. Users are
created exclusively as local users via CLI.

**Candidate mechanism**: SCIM 2.0 push from SUSEID (Authentik) with
hourly full reconciliation sync. The application would expose
`/scim/v2/Users` and `/scim/v2/Groups` endpoints as a SCIM Service
Provider.

**Open questions** (to be resolved before this spec is enabled):
- Authentication: static bearer token (confirmed) — rotation mechanism?
- Scope: all employees regardless of active status (requested, pending confirmation)
- Retry/delivery guarantee: dramatiq defaults + hourly full sync as catch-up
- Group naming: original Okta group names preserved (confirmed)
- Rate/concurrency: 2-4 workers, sequential-ish (low pressure)
- Testing: staging SCIM provider in dry-run mode (offered by infra team)

## Bootstrap Sequence (External Provisioning)

[Adapted from ad-integration.md, generalized:
1. Create local admin via CLI
2. Configure external provisioning endpoint
3. Trigger initial full sync (or wait for hourly cycle)
4. Promote an external user to admin
5. Deactivate bootstrap account (recommended)]

## Future Evolution

When this spec is enabled and finalized:
1. Define the SCIM Service Provider endpoint contract
2. Define reconciliation fetcher (if pull-based catch-up is needed)
3. Finalize group validation rules for the provider
4. Update SSO spec to remove "deferred" notes
5. Add fetcher source_type value if a reconciliation fetcher is added
6. Re-enable sso-authentication in .tracking.json

## Cross-references

- docs/features/identity/user-service.md — external user data ownership,
  sync_role_mapping(), delete_role_mapping_roles()
- docs/features/identity/rbac.md — manage_role_mappings capability,
  Role Origins and Coexistence
- docs/features/identity/sso-authentication.md — SSO requires external
  provisioning
- docs/data-model.md — User, UserRole, RoleMapping tables
- docs/api-spec.md — PROVISIONING_UNAVAILABLE, ROLE_MAPPING_* error codes
```

### Step 2 — Delete `docs/features/identity/ad-integration.md`

**Action**: delete the file entirely.

### Step 3 — Generalize `docs/data-model.md`

**Changes** (all within the Identity section):

1. **User table** (lines ~928-957):
   - Description: "populated from SUSE Active Directory via the
     `sync_ldap_directory` fetcher" → "populated from an external
     identity provider (see
     `docs/features/identity/identity-provisioning.md`) or created
     locally via CLI"
   - Column `username`: "(from AD `sAMAccountName`). Updated by LDAP
     sync if `sAMAccountName` changes in AD" → "Login username.
     Updated by external sync if changed at the provider"
   - Column `email`: "(from AD `mail`; stored as lowercase)" → "Email
     address (stored as lowercase)"
   - Column `full_name`: "(from AD `cn`)" → "Display name"
   - Column `active`: "(synced from AD `EMPLOYEESTATUS`)" → "Whether
     the account is active. For external users, synced from the
     identity provider"
   - Column `ad_object_guid` → `external_id`: "AD `objectGUID`
     (immutable after creation). Used as the stable matching key during
     LDAP sync. NULL for local users" → "Stable external identifier
     from the identity provider (immutable after creation). Used as the
     matching key during external sync. NULL for local users"
   - Column `ad_synced_at` → `synced_at`: "When this record was last
     synced from AD" → "When this record was last synced from the
     external provider"
   - Column `manager_id`: "(resolved from AD `manager` DN during
     sync)" → "(resolved from external provider's manager reference
     during sync)"
   - Check constraint: "`ad_object_guid`" → "`external_id`";
     description: "AD users cannot have a password, local users must
     have a password" → "External users cannot have a password, local
     users must have a password"

2. **ERD** (lines ~254-312):
   - `UUID ad_object_guid` → `UUID external_id`
   - `VARCHAR_256 ad_group_cn` → `VARCHAR_256 group_name` (UserRole)
   - `VARCHAR_256 ad_group_cn` → `VARCHAR_256 group_name` (RoleMapping)

3. **UserRole table** (lines ~959-998):
   - Description: "if it contains an AD group common name, the role was
     derived from that group's RoleMapping" → "if it contains an
     external group name, the role was derived from that group's
     RoleMapping"
   - "Roles with `ad_group_cn != '_manual'` are managed by the LDAP
     sync process" → "Roles with `group_name != '_manual'` are managed
     by the external sync process"
   - Cross-ref: `ad-integration.md` → `identity-provisioning.md`
   - Column: `ad_group_cn` → `group_name`; description: "AD group CN
     that granted this role, or `_manual`" → "External group name that
     granted this role, or `_manual` for manual assignments"
   - Column: `assigned_by` description: "NULL for system actions (LDAP
     sync, CLI)" → "NULL for system actions (external sync, CLI)"
   - Unique constraint: `(user_id, role, ad_group_cn)` → `(user_id,
     role, group_name)`
   - `ad_group_cn semantics` → `group_name semantics`:
     - "`_manual`" → unchanged
     - "Any other value: AD group common name — role derived from that
       group's RoleMapping rule" → "Any other value: external group
       name — role derived from that group's RoleMapping rule"

4. **RoleMapping table** (lines ~1000-1016):
   - Description: "Stores the mapping rules between Active Directory
     groups and Sentinel roles... During the daily LDAP sync..." →
     "Stores the mapping rules between external identity provider
     groups and Sentinel roles... During external provisioning sync..."
   - Cross-ref: `ad-integration.md` → `identity-provisioning.md`
   - Column: `ad_group_cn` → `group_name`; description: "AD group
     common name" → "External group name"
   - Unique constraint: `(ad_group_cn, role)` → `(group_name, role)`

5. **IdentityAuditEvent section** (lines ~1262-1293):
   - All occurrences of "AD sync" in event descriptions → "external
     sync"
   - `username_changed` trigger: "AD sync detects sAMAccountName
     change" → "External sync detects username change at provider"
   - `manager_changed` trigger: "Direct manager updated (AD sync)" →
     "Direct manager updated (external sync)"
   - `role_mapping_created/deleted`: "AD group-to-role mapping" →
     "Group-to-role mapping"
   - `role_added/removed` detail: `"mapping": "cn=SecurityTeam"` →
     `"mapping": "SecurityTeam"`

6. **Notes section** (line ~1525): reference to `RoleMapping` as
   "created_at only / write-once" table — no change needed (neutral).

### Step 4 — Generalize `docs/features/identity/user-service.md`

**Changes**:

1. **Purpose** (line 8): "LDAP sync" → "external sync"

2. **Async pattern table** (line 32): "Celery task (LDAP)" → "Celery
   task (sync)"

3. **Acting user convention** (line 44): "LDAP sync" → "external sync"

4. **AD User Data Ownership** section (lines 54-79) → rename to
   **"External User Data Ownership"**:
   - "For AD users (`ad_object_guid IS NOT NULL`)" → "For external
     users (`external_id IS NOT NULL`)"
   - "LDAP sync fetcher" → "external sync process"
   - "managed exclusively by the LDAP sync fetcher" → "managed
     exclusively by the external sync process"
   - "`ad_synced_at`" → "`synced_at`"
   - All occurrences of "AD users" → "external users"
   - Cross-ref: `ad-integration.md` → `identity-provisioning.md`

5. **AD Active Status Ownership** (lines 81-105) → rename to
   **"External Active Status Ownership"**:
   - "Active Directory `EMPLOYEESTATUS`" → "The external identity
     provider"
   - "AD users" → "external users"
   - "deactivate the employee in Active Directory" → "deactivate the
     user at the external identity provider"
   - "`ADUserStatusReadOnlyError`" → "`ExternalUserStatusReadOnlyError`"
   - "LDAP sync" → "external sync"

6. **Immutability Constraints** (lines 107-113): "`ad_object_guid`" →
   "`external_id`"; "Active Directory object" → "external provider
   object"; "LDAP sync operations" → "external sync operations"

7. **`create_user()` parameters** (lines 246-257): `ad_object_guid` →
   `external_id`; description: "AD `objectGUID`" → "External provider
   stable UUID"

8. **`create_user()` behavior** (lines 259-298):
   - Step 2: "`ad_object_guid`" → "`external_id`";
     "`ADUserPasswordError`" → "`ExternalUserPasswordError`"
   - Step 3: "`ad_object_guid`" → "`external_id`"
   - Step 6: "`ad_object_guid`" → "`external_id`"; "`ad_synced_at =
     now()`" → "`synced_at = now()`"

9. **`update_user()` parameters** (lines 314-323): "`ad_synced_at`" →
   "`synced_at`"

10. **`update_user()` behavior** (lines 325-365):
    - Step 2: "`user.ad_object_guid IS NOT NULL`" → "`user.external_id
      IS NOT NULL`"; "`ADUserFieldReadOnlyError`" →
      "`ExternalUserFieldReadOnlyError`"; "directory sync" → "external
      sync"
    - Step 3: "`user.ad_object_guid IS NULL`" → "`user.external_id IS
      NULL`"; "`manager_id` or `ad_synced_at`" → "`manager_id` or
      `synced_at`"; "`ADUserFieldReadOnlyError`" →
      "`ExternalUserFieldReadOnlyError`"; "AD-specific" → "external-
      provider-specific"
    - Step 4: "AD users"→"external users"

11. **`update_roles()` parameters and business rules** (lines 383-415):
    - Parameters (383-384): "Roles to add as (role, ad_group_cn)" →
      "Roles to add as (role, group_name)"; same for `remove`
    - Business rule 2 (398-401): "AD-derived role protection" →
      "External-derived role protection"; "`ad_group_cn != '_manual'`"
      → "`group_name != '_manual'`"; "`ADDerivedRoleError`" →
      "`DerivedRoleError`"; "LDAP sync" → "external sync"
    - Behavior (403, 410): "`(user_id, role, ad_group_cn)`" →
      "`(user_id, role, group_name)`"; "`(role, ad_group_cn)`" →
      "`(role, group_name)`"
    - Step 3 (415): "AD-derived protection" → "external-derived
      protection"

12. **`sync_role_mapping()` preamble** (lines 445-455): "current set
    of AD group members" → "current set of group members"; "all bulk
    role operations triggered by AD group membership" → "all bulk role
    operations triggered by external group membership"; "called by
    the LDAP sync fetcher" → "called by the external sync process";
    "User IDs currently in the AD group" → "User IDs currently in
    the group"

13. **`sync_role_mapping()` parameters** (lines 456-461):
    "`ad_group_cn`" → "`group_name`"; description: "The AD group CN" →
    "The external group name"

14. **`sync_role_mapping()` behavior** (lines 463-496): all "`role`
    and `ad_group_cn`" → "`role` and `group_name`"; "AD group members"
    → "group members"; "`ad_group_cn`" → "`group_name`" throughout;
    `"source": "ad_sync"` → `"source": "external_sync"` (line 488)

15. **`delete_role_mapping_roles()` parameters** (lines 516-519):
    "`ad_group_cn`" → "`group_name`"

16. **`delete_role_mapping_roles()` behavior** (lines 523-541): all
    "`ad_group_cn`" → "`group_name`"

17. **`deactivate_user()` preconditions** (lines 566-573):
    "`user.ad_object_guid IS NOT NULL`" → "`user.external_id IS NOT
    NULL`"; "`ADUserStatusReadOnlyError`" →
    "`ExternalUserStatusReadOnlyError`"; "directory sync" → "external
    sync"

18. **`deactivate_user()` IdentityAuditEvent** (line 607): "`NULL`
    for AD sync" → "`NULL` for external sync"

19. **`reactivate_user()` preconditions** (lines 630-633): same
    substitutions as deactivate.

20. **`reset_password()` preconditions** (lines 668-671):
    "`ad_object_guid`" → "`external_id`"; "`ADUserPasswordError`" →
    "`ExternalUserPasswordError`"; "AD user" → "external user";
    "authenticate via id.suse.com" → "authenticate via SSO"

21. **Concurrency Considerations** (lines 786-803): "LDAP sync" →
    "external sync"; "manual actions use `ad_group_cn = '_manual'`
    while LDAP sync uses the actual AD group CN" → "manual actions use
    `group_name = '_manual'` while external sync uses the actual group
    name"

22. **Service Exceptions table** (lines 820-833):
    - `ADUserStatusReadOnlyError` → `ExternalUserStatusReadOnlyError`
      | 409 | `USER_EXTERNAL_STATUS_READONLY` | "Cannot manually
      activate/deactivate an external user"
    - `ADDerivedRoleError` → `DerivedRoleError` | 409 |
      `USER_EXTERNAL_ROLE_PROTECTED` | "Cannot manually modify
      externally-derived roles"
    - `ADUserFieldReadOnlyError` → `ExternalUserFieldReadOnlyError` |
      409 | `USER_EXTERNAL_FIELD_READONLY` | "Cannot modify synced
      fields on an external user"
    - `ADUserPasswordError` → `ExternalUserPasswordError` | 409 |
      `USER_EXTERNAL_PASSWORD_FORBIDDEN` | "Cannot set password for
      an external user"

23. **System-internal exceptions** (line 839): "LDAP sync: logged as
    warning, user skipped" → "External sync: logged as warning, user
    skipped"

24. **Relationship table** (line 847): `ad-integration.md` →
    `identity-provisioning.md`; "LDAP sync fetcher calls..." →
    "External sync process calls..."

### Step 5 — Generalize `docs/features/identity/rbac.md`

**Changes**:

1. **Capability table** (line 37): "AD role mapping CRUD, preview role
   mapping" → "Group-to-role mapping CRUD, preview role mapping"

2. **Permission Matrix** (lines 162-163): "AD role mapping CRUD" →
   "Group-to-role mapping CRUD"; "Preview role mapping" → unchanged

3. **Endpoint Permission Map — Administration section** (lines
   487-490):
   - Role-mapping endpoints: owning spec link changes from
     `[ad-integration](ad-integration.md#...)` →
     `[identity-provisioning](identity-provisioning.md#...)` (with
     appropriate anchor fragments)

4. **Notes after Endpoint Permission Map** (lines 505-507): "AD users
   are created by the LDAP directory sync (see
   [ad-integration](ad-integration.md#ldap-sync-fetcher))" → "External
   users are created by the external provisioning process (see
   [identity-provisioning](identity-provisioning.md)); local users are
   created by admins via CLI (see
   [user-management](user-management.md#cli-commands))"

5. **Business Rules** (lines 517-559):
   - Rule 1: "System actions (LDAP sync, CLI)" → "System actions
     (external sync, CLI)"
   - Rule 4: "AD users cannot be manually deactivated or reactivated —
     their active status is controlled exclusively by Active Directory
     via LDAP sync" → "External users cannot be manually deactivated or
     reactivated — their active status is controlled exclusively by the
     external identity provider via sync"
   - Rule 9: entire admin bootstrap reference: "trigger the LDAP sync
     (`POST /api/v1/fetchers/sync_ldap_directory/trigger`)" → "trigger
     the external provisioning sync (see
     `docs/features/identity/identity-provisioning.md` for the
     bootstrap sequence when external provisioning is active)"; keep
     the local-admin CLI part intact

6. **Role Origins and Coexistence** (lines 678-711):
   - "AD-derived" → "Externally-derived"
   - "`ad_group_cn = <group CN>`" → "`group_name = <group name>`"
   - "`ad_group_cn = '_manual'`" → "`group_name = '_manual'`"
   - "LDAP sync" → "external sync"
   - Cross-ref: `ad-integration.md` → `identity-provisioning.md`

7. **Data Model section** (lines 650-658): "AD fields
   (ad_object_guid, manager_id, ad_synced_at)" → "External identity
   fields (external_id, manager_id, synced_at)"; "`ad_group_cn`" →
   "`group_name`"; "AD group name or `_manual`" → "external group name
   or `_manual`"

8. **Cross-references** (line 717): `ad-integration.md` →
   `identity-provisioning.md`

### Step 6 — Generalize `docs/features/identity/identity-audit-log.md`

**Changes**:

1. **IdentityAuditEventType table** (lines 51-66):
   - `user_created` trigger: "(manual or AD sync)" → "(manual or
     external sync)"
   - `user_deactivated`: "Admin or AD sync" → "Admin or external sync";
     detail example `"ad_sync_missing"` → `"external_sync_missing"`
   - `role_added/removed`: "Admin or AD sync" → "Admin or external
     sync"; detail: `"ad_sync"` → `"external_sync"`, `"mapping":
     "cn=SecurityTeam"` → `"mapping": "SecurityTeam"`
   - `role_mapping_created/deleted`: "AD group-to-role mapping" →
     "Group-to-role mapping"; detail: `"ad_group_cn"` → `"group_name"`
   - `username_changed`: "AD sync detects sAMAccountName change" →
     "External sync detects username change at provider"
   - `manager_changed`: "(AD sync)" → "(external sync)"
   - `email_changed/full_name_changed`: "(admin or AD sync)" →
     "(admin or external sync)"

2. **detail JSONB Schema Contract** (lines 70-101):
   - `role_added/removed`: `"source": "ad_sync"` → `"source":
     "external_sync"`; `"mapping": "cn=SecurityTeam"` → `"mapping":
     "SecurityTeam"`
   - `role_mapping_created/deleted`: `"ad_group_cn"` → `"group_name"`

3. **Service Contract prose** (lines 259-270): "AD sync" → "external
   sync" throughout

4. **Cross-references** (line 301): `ad-integration.md` →
   `identity-provisioning.md`

### Step 7 — Generalize `docs/features/identity/user-management.md`

**Changes**:

1. **Purpose** (lines 7-8): "AD users (synced from SUSE Active
   Directory via LDAP)" → "external users (provisioned from an external
   identity provider)"

2. **Purpose prose** (lines 21-27): "AD users are provisioned and
   maintained by the LDAP sync process (see
   `docs/features/identity/ad-integration.md`). Administrators can
   modify their roles, but cannot deactivate/reactivate them (active
   status is managed exclusively by directory sync), set passwords, or
   create them manually." → "External users are provisioned and
   maintained by the external identity provider (see
   `docs/features/identity/identity-provisioning.md`). Administrators
   can modify their roles, but cannot deactivate/reactivate them
   (active status is managed exclusively by external sync), set
   passwords, or create them manually."
   Lines 26-27: "bypassing the LDAP sync process. They are
   functionally identical to AD-synced users" → "bypassing the
   external provisioning process. They are functionally identical to
   externally-provisioned users"

3. **`manage-user create`** (line 93): `ad_object_guid = None` →
   `external_id = None`

4. **`manage-user update`** (lines 117-236):
   - "AD users have their identity fields managed exclusively by
     directory sync" → "External users have their identity fields
     managed exclusively by external sync"
   - "`ad_object_guid IS NOT NULL`" → "`external_id IS NOT NULL`"
   - Error messages: "managed by Active Directory via LDAP sync" →
     "managed by an external identity provider"
   - "Cannot reactivate AD users" → "Cannot reactivate external users"

5. **`manage-user deactivate`** (lines 248-319):
   - "`ad_object_guid IS NOT NULL`" → "`external_id IS NOT NULL`"
   - "Cannot deactivate AD users" → "Cannot deactivate external users"
   - "This is consistent with LDAP sync deactivation behavior" →
     "This is consistent with external sync deactivation behavior"

6. **`manage-user set-password`** (lines 321-363):
   - "`ad_object_guid = NULL`" → "`external_id = NULL`"
   - "Cannot set password for AD user" → "Cannot set password for
     external user"
   - "AD users authenticate via id.suse.com" → "External users
     authenticate via SSO"

7. **`manage-user unlock`** (lines 365-413):
   - "`ad_object_guid IS NOT NULL`" → "`external_id IS NOT NULL`"
   - "AD user" → "external user"
   - "Local login lockout does not apply to SSO authentication" →
     unchanged (still correct)

8. **`manage-user list`** (lines 415-462): `--type local|ad` → `--type
   local|external`; TYPE column in output example: `ad` → `external`

9. **`manage-user show`** (lines 464-511): "Type: local" unchanged,
   AD group CN references → "group name"

10. **Public API endpoints — List Users** (line 534): `type` query
    parameter values: `local`, `ad` → `local`, `external`

11. **Public API endpoints — Get User response** (lines 547-589):
    - `"source": "ad | local"` → `"source": "external | local"`
    - `"ad_object_guid": "uuid | null"` → `"external_id": "uuid | null"`
    - Field notes: `source` derived `"ad"` → `"external"`; "AD
      `objectGUID`" → "External provider stable UUID"
    - `"ad_group_cn"` → `"group_name"`

12. **Admin API endpoints**:
    - Update User (lines 607-641): "AD user" → "external user";
      "`ad_object_guid IS NOT NULL`" → "`external_id IS NOT NULL`";
      `USER_AD_FIELD_READONLY` → `USER_EXTERNAL_FIELD_READONLY`;
      "managed by Active Directory" → "managed by external identity
      provider"
    - Set User Roles (lines 647-694): "`ad_group_cn != '_manual'`" →
      "`group_name != '_manual'`"; `USER_AD_ROLE_PROTECTED` →
      `USER_EXTERNAL_ROLE_PROTECTED`; "Cannot remove AD-derived role"
      → "Cannot remove externally-derived role"; "AD group
      '{ad_group_cn}'" → "external group '{group_name}'"; "via AD
      derivation" → "via external derivation"; "`ad_group_cn =
      '_manual'`" → "`group_name = '_manual'`"
    - Reset User Password (lines 696-737): "AD user" → "external
      user"; `USER_AD_PASSWORD_FORBIDDEN` →
      `USER_EXTERNAL_PASSWORD_FORBIDDEN`; "Cannot set password for AD
      user" → "Cannot set password for external user"
    - Deactivate User (lines 739-768): `USER_AD_STATUS_READONLY` →
      `USER_EXTERNAL_STATUS_READONLY`; "Cannot deactivate AD users" →
      "Cannot deactivate external users"
    - Reactivate User (lines 774-798): same substitution
    - Get Deactivation Impact (lines 800-873): same substitution

13. **Interaction with LDAP Sync section** (lines 900-911) → rename to
    **"Interaction with External Provisioning"**: "`ad_object_guid IS
    NOT NULL`" → "`external_id IS NOT NULL`"; "`ad_object_guid = NULL`"
    → "`external_id = NULL`"; "sync process" → "external sync process";
    "AD-derived roles" → "externally-derived roles"

14. **Business Rules** (lines 913-941): "`ad_object_guid = NULL`" →
    "`external_id = NULL`"; ref to `ad-integration.md` →
    `identity-provisioning.md`; "`ad_group_cn = '_manual'`" →
    "`group_name = '_manual'`"

15. **Cross-references** (line 1001): `ad-integration.md` →
    `identity-provisioning.md`

### Step 8 — Generalize `docs/features/identity/sso-authentication.md`

**Changes**:

1. **Purpose** (lines 10-20): "synced into Sentinel via the
   `sync_ldap_directory` fetcher" → "provisioned in Sentinel by an
   external identity provider (see
   `docs/features/identity/identity-provisioning.md`)"; "`ad_object_guid
   IS NOT NULL`" → "`external_id IS NOT NULL`"; "`ad_object_guid =
   NULL`" → "`external_id = NULL`"

2. **Configuration table** (line 47): "matched against `username` for
   AD-synced users" → "matched against `username` for externally-
   provisioned users"

3. **Callback behavior** (lines 271-273): "`ad_object_guid IS NOT
   NULL`" → "`external_id IS NOT NULL`"; "AD-synced user" → "externally
   provisioned user"

4. **Identity Mapping** (lines 366-413):
   - "`ad_object_guid IS NOT NULL`" → "`external_id IS NOT NULL`"
   - "The `sync_ldap_directory` fetcher imports users from SUSE Active
     Directory and stores their `sAMAccountName` as `username`" → "The
     external provisioning process imports users and stores their
     provider username as `username`"
   - "`id.suse.com` uses the same AD as its identity source, so its
     `sub` claim corresponds to the `sAMAccountName`" → "`id.suse.com`
     uses the same identity source, so its `sub` claim corresponds to
     the provider username"
   - "consistency with the `sync_ldap_directory` fetcher, which stores
     `sAMAccountName` normalized to lowercase" → "consistency with the
     external sync process, which stores usernames normalized to
     lowercase"
   - "AD `sAMAccountName` is inherently case-insensitive" → "provider
     usernames may be case-insensitive"

5. **No auto-provisioning** (lines 399-413): "created by the LDAP sync
   process" → "created by the external provisioning process"; "managed
   AD groups" → "external groups"; "LDAP sync as the single source of
   truth for AD user provisioning" → "external provisioning as the
   single source of truth for external user accounts"; cross-ref →
   identity-provisioning.md

6. **Security Considerations** (line 479): "only access control that
   applies to AD users" → "only access control that applies to external
   users"

7. **Cross-references** (line 534): "LDAP sync that provisions SSO user
   accounts" → `identity-provisioning.md` — "External provisioning
   (deferred) for SSO user accounts"

### Step 9 — Generalize `docs/features/identity/local-authentication.md`

**Changes**:

1. **Purpose** (lines 24-27): "`ad_object_guid = NULL`" → "`external_id
   = NULL`"; "Users managed by LDAP sync (`ad_object_guid IS NOT
   NULL`)" → "Users managed by external provisioning (`external_id IS
   NOT NULL`)"

2. **Login behavior step 8** (line 67): "`ad_object_guid IS NOT NULL`
   (AD user)" → "`external_id IS NOT NULL` (external user)"; "AD users
   cannot use local login" → "External users cannot use local login"

3. **Password Storage** (lines 114-119): "NULL for AD users (who
   authenticate via id.suse.com and never have a local password)" →
   "NULL for external users (who authenticate via SSO and never have a
   local password)"; "`chk_user_auth_exclusive`" → unchanged (neutral);
   ref unchanged

4. **Why bcrypt** (lines 143-150): "The primary authentication path for
   SUSE employees is SSO" → unchanged (still true)

5. **Admin UI** (lines 201-203): "For AD users" → "For external users"

### Step 10 — Verify `docs/features/identity/authentication.md`

**Action**: no changes needed.

The file contains no references to "AD user", "LDAP", "Active
Directory", `ad_object_guid`, or any AD-specific terminology (the only
occurrence of "AD" is "Azure AD" in the accepted-risk section about
OIDC, which is unrelated). The deactivation prose already references
`user_service` generically. Verified 2026-07-16.

### Step 11 — Generalize `docs/features/identity/README.md`

**Replace entire content with**:

```markdown
# Identity

User authentication, authorization, and lifecycle management.

## Specs

```
authentication.md              Session/JWT/API-key framework (umbrella)
├── sso-authentication.md      OIDC SSO login flow (deferred)
└── local-authentication.md    Username/password login, lockout

identity-provisioning.md       External provisioning, role mapping (deferred)
user-service.md                Service-layer contract for user mutations
user-management.md             Admin CLI and API for user operations
rbac.md                        Role definitions and endpoint permission map
identity-audit-log.md          Identity audit trail (IdentityAuditEvent)
api-key-service.md             API key lifecycle management
```

## Relationships

- `authentication.md` is the parent spec for SSO and local login —
  shared concerns (session lifecycle, token format, API keys) are
  defined there and inherited by sub-specs.
- `identity-provisioning.md` (deferred) defines how external users are
  provisioned and how group memberships map to roles; `rbac.md` defines
  what those roles grant.
- `user-service.md` is the centralized service contract consumed by
  `user-management.md`, `identity-provisioning.md`, and any future
  entry point that mutates users.
```

### Step 12 — Remove LDAP mechanism from `docs/configuration.md`

**Changes**:

1. **Delete the "LDAP Directory Sync" section** (lines 160-173)
   entirely (the `LDAP_URI` variable and the note about custom
   settings).

2. **SSO section** (line 129): "matched against `username` for
   AD-synced users" → "matched against `username` for externally-
   provisioned users"

3. `SUSE_CA_CERT_PATH` description (line 165): remove "LDAP," from the
   list — becomes "Path to SUSE internal CA certificate for TLS
   validation of all connections to *.suse.de services (HTTP, AMQP).
   Combined with system CA bundle at runtime."

### Step 13 — Remove LDAP from `docs/architecture.md`

**Changes**:

1. **Replace the "SUSE Active Directory" section** (lines 198-213) with
   a shorter section:

   ```markdown
   #### External Identity Provider

   - External user provisioning is deferred to a future phase. See
     `docs/features/identity/identity-provisioning.md` for the planned
     approach (SCIM-based push from SUSEID)
   - In the current phase, only local user accounts are supported
     (created via CLI)
   - See `docs/features/identity/user-management.md` for local user
     management
   ```

2. In the **Data Flow** section, if there is any mention of "LDAP sync"
   in the user provisioning flow, replace with a reference to
   identity-provisioning.md (deferred).

### Step 14 — Remove LDAP from `docs/data-sources.md`

**Changes**:

1. **Catalog table** (lines 28-29): remove the two rows "SUSE Active
   Directory" and "SUSE OpenLDAP".

2. **Delete the entire "Identity and Directory Services" section**
   (lines 694-781) — covers both SUSE AD and SUSE OpenLDAP.

3. **Fetcher Registry table** (line ~952): remove the
   `sync_ldap_directory` row.

### Step 15 — Remove LDAP from `docs/deployment.md`

**Changes**:

1. **Connectivity table** (line 32): remove the row "SUSE AD |
   pan.suse.de | 636 | LDAP directory sync".

2. **Staging config table** (line 167): remove the `LDAP_URI` row.

3. **Staging note** (lines 191-192): remove "LDAP sync runs on the same
   schedule as production (daily) — staging has real user data from AD".

4. **Production checklist** (line 229): remove "SUSE Trust Root CA
   installed in container for LDAP TLS validation" OR reword to remove
   "LDAP" (the CA is still needed for HTTPS/AMQP to other `*.suse.de`
   services). Reword to: "SUSE Trust Root CA installed in container for
   TLS validation of *.suse.de services"

5. **SSO Login Fails troubleshooting** (line 526): "`ad_object_guid IS
   NOT NULL` (run LDAP sync first)" → "`external_id IS NOT NULL` (the
   user must be provisioned via external identity provider first — see
   `identity-provisioning.md`)"

6. **Delete "LDAP Sync Not Working" troubleshooting section** (lines
   528-533).

### Step 16 — Remove LDAP from `docs/features/platform/networking.md`

**Changes**:

1. **Intro** (line 7): remove "`sync_ldap_directory` (LDAP)" from the
   list of TLS clients. Keep the shared client, IBSClient, and
   IBSEventConsumer.

2. **TLS Trust Store section** (lines 446-447): "HTTP (shared client),
   LDAP (`sync_ldap_directory`), AMQP (`IBSEventConsumer`)" → "HTTP
   (shared client) and AMQP (`IBSEventConsumer`)"

3. **`build_tls_context()` usage** (line 523): "Non-HTTP components
   (`sync_ldap_directory`, `IBSEventConsumer`)" → "Non-HTTP components
   (`IBSEventConsumer`)"

4. **Protocol table** (line 537): remove the LDAPS row entirely.

5. **Cross-references** (line 550): remove the cross-ref to
   `ad-integration.md`.

### Step 17 — Remove LDAP from `docs/features/platform/fetcher-infrastructure.md`

**Changes**:

1. **Source-component enum table** (line 294): remove the `ldap` row
   entirely.

2. **Naming example table** (line 672): remove the
   `sync_ldap_directory` row.

3. **Hostname example** (line 805): remove `pan.suse.de` from the list
   of internal hostnames (keep `build.suse.de`, `smelt.suse.de`,
   `rabbit.suse.de`).

4. **Config classification example** (line 852): remove `LDAP_URI` from
   the example — keep `IBS_API_URL` as the sole example.

5. **Prose** (line 363): "AD sync, etc." → remove "AD sync" or replace
   with another example of a non-CVE fetcher.

### Step 18 — Remove LDAP from `docs/features/platform/fetcher-operations.md`

**Changes**:

1. **CLI dashboard mock output** (line 809): remove the
   `sync_ldap_directory` line from the example output.

### Step 19 — Generalize `docs/features/platform/audit-trail-infrastructure.md`

**Changes**:

1. Lines 252, 319: "AD sync" → "external sync"

### Step 20 — Generalize `docs/api-spec.md`

**Changes**:

1. **Error Code Categories table** (line 152): `ROLE_MAPPING_*`
   description: "Role mapping operations" → unchanged. Specific codes:
   `ROLE_MAPPING_INVALID_GROUP_CN` → `ROLE_MAPPING_INVALID_GROUP_NAME`

2. **Error Code Categories table** (line 155): `USER_*` row — rename
   4 codes and remove 1 orphaned code:
   - `USER_AD_STATUS_READONLY` → `USER_EXTERNAL_STATUS_READONLY`
   - `USER_AD_FIELD_READONLY` → `USER_EXTERNAL_FIELD_READONLY`
   - `USER_AD_PASSWORD_FORBIDDEN` → `USER_EXTERNAL_PASSWORD_FORBIDDEN`
   - `USER_AD_ROLE_PROTECTED` → `USER_EXTERNAL_ROLE_PROTECTED`
   - `USER_AD_LOCKOUT` → **remove entirely** (orphaned — registered in
     commit 431b8ca as part of a batch expansion but never referenced by
     any endpoint or service exception in any feature spec)

3. **Resource-unavailable codes** (line 183): `AD_UNAVAILABLE | Active
   Directory (via LDAP)` → `PROVISIONING_UNAVAILABLE | External
   identity provider`

4. **Username mutability rationale** (lines 593, 630): "via AD sync" →
   "via external sync"

### Step 21 — Generalize `docs/conventions.md`

**Changes**:

1. **Replace "Active Directory / LDAP / SSO Terminology" section**
   (lines 53-70) with the new "External Identity / SSO Terminology"
   section defined in the Naming Scheme above.

2. **Cascade/Chain/Flattening** (line 91): "manager chain (reporting
   hierarchy in `ad-integration.md`)" → "manager chain (reporting
   hierarchy — see `docs/features/identity/identity-provisioning.md`)"

3. **PII rule** (line 26): remove "AD" from the list of external
   systems → "(IBS, SMELT, Bugzilla, NVD, etc.)"

4. **Transaction/Locking** (line 346): "IBS, SMELT, NVD, AIMAAS, AD,
   or any network I/O" → "IBS, SMELT, NVD, AIMAAS, or any network I/O"

5. **Example Data table** (line 38): remove the "LDAP DNs" row (no
   longer relevant) OR replace with a generic "External IDs" row if
   needed as a placeholder pattern.

### Step 22 — Generalize `docs/features/packages/package-bugowner.md`

**Changes**:

1. Line 212: "(also stored as lowercase from AD sync)" → "(also stored
   as lowercase)"

### Step 23 — Update `docs/features/README.md`

**Changes**:

1. Remove the line referencing `ad-integration.md`.
2. Add a line for `identity-provisioning.md` — "External identity
   provisioning, role mapping (deferred)"
3. Update `sso-authentication.md` description to note "(deferred)"

### Step 24 — Update `docs/system-map.md` (git-ignored)

**Changes**:

1. **Mermaid architecture diagram**: replace `LDAP["SUSE AD<br/>
   (pan.suse.de)"]` node and `WORKER -->|"LDAPS (port 636)"| LDAP`
   edge with a generic placeholder or remove entirely.

2. **ERD**: `ad_object_guid` → `external_id`; UserRole/RoleMapping
   `ad_group_cn` → `group_name`.

3. **Domain table**: RoleMapping row — unchanged (neutral).

4. **Spec-dependency graph**: replace `ADI["ad-integration"]` node with
   `IDP["identity-provisioning"]`; update edge references.

5. **Spec index**: replace ad-integration row with
   identity-provisioning row.

### Step 25 — Update `docs/reviews/.tracking.json`

**Changes**:

1. **Remove** the `"ad-integration"` entry entirely.

2. **Add** a new entry:
   ```json
   "identity-provisioning": {
     "enabled": false,
     "abbr": "IDP",
     "cache": null
   }
   ```

3. **Set** `"sso-authentication"` → `"enabled": false`.

### Step 26 — Update `docs/reviews/README.md`

**Changes**:

1. Remove the `ad-integration` row from the status table.
2. Add an `identity-provisioning` row (disabled, no review).
3. Update `sso-authentication` row to reflect disabled status.

### Step 27 — Verify `AGENTS.md`

**Action**: no changes needed.

The current `AGENTS.md` (892 lines) contains no references to "LDAP",
`ad-integration.md`, `sync_ldap_directory`, `LDAP_URI`, `ad_object_guid`,
or "AD" as a data-origin label. All three guardrails cited in the
original plan are already provider-neutral:

- **Guardrail 11** (Identity audit trail): references
  `identity-audit-log.md`, not `ad-integration.md`; no mention of
  "LDAP sync"
- **Guardrail 14** (Fetcher base class compliance): lists "CVE sync,
  CVSS sync, product sync, release detection" — no mention of
  `sync_ldap_directory`
- **Guardrail 19** (Centralized user lifecycle operations): references
  `user_service` module — no "Celery task (LDAP)" pattern

The original plan confused AGENTS.md content with the content of files
it references (`user-service.md` contains "Celery task (LDAP)" at line
32 — handled by Step 4.2; `identity-audit-log.md` references
`ad-integration.md` at line 300 — handled by Step 6.4). Verified
2026-07-16.

The verification sweep in Step 28 includes `AGENTS.md` in its
repository-wide search, providing an additional safety net.

### Step 28 — Verification sweep

**Action**: perform a repository-wide search (case-insensitive) for:
- `ldap`
- `pan.suse.de`
- `ldap.suse.de`
- `ad_object_guid`
- `ad_synced_at`
- `ad_group_cn`
- `sync_ldap_directory`
- `SyncLdapDirectory`
- `LDAP_URI`
- `AD_UNAVAILABLE`
- `ROLE_MAPPING_INVALID_GROUP_CN`
- `ADUser` (as error class prefix)
- `USER_AD_`
- `ad_sync` (JSON value in audit event detail)
- `EMPLOYEESTATUS`
- `sAMAccountName`
- `objectGUID`
- `MEMBEROF`
- `anonymous bind`

**Expected result**: zero matches in `docs/` (excluding
`docs/reviews/*.md` review artifacts which are historical and
untracked). If any matches remain, resolve them before proceeding.

**Exception**: `docs/reviews/` artifacts are historical findings and do
not need updating — they document past review results against the
old spec. They are untracked by git and do not affect implementation.

### Step 29 — Run reviewer agents

Run the following reviewer agents on the relevant specs to verify the
changes were applied correctly and without introducing problems:

| Reviewer | Target specs | Purpose |
|----------|-------------|---------|
| `@data-model-reviewer` | After editing `docs/data-model.md` | Verify schema rename is consistent, no orphaned references |
| `@spec-coherence-reviewer` | `user-service.md`, `rbac.md`, `user-management.md`, `identity-audit-log.md`, `sso-authentication.md`, `local-authentication.md`, `authentication.md` (one per session) | Detect contradictions between updated specs |
| `@api-convention-reviewer` | `identity-provisioning.md` (deferred endpoints) | Verify endpoint contracts follow API conventions |
| `@docs-reviewer` | After all edits complete | Verify documentation completeness and cross-ref integrity |
| `@docs-placement-reviewer` | `identity-provisioning.md` + cross-cutting docs | Verify information is placed correctly |
| `@spec-gap-analyzer` | `identity-provisioning.md` | Identify uncovered cases in the new deferred spec |

Address any issues found by reviewers before considering the plan
complete.

### Step 30 — Delete this draft

**Action**: delete `docs/drafts/ldap-removal-plan.md`.

This draft is a transitional artifact. Once the plan has been executed
and verified, it has no ongoing value — the specifications themselves
are the source of truth.

---

## Scope exclusions

- **`docs/reviews/*.md`** (review artifacts): historical, untracked by
  git, do not affect implementation. Not modified.
- **Implementation code**: does not exist. No code changes.
- **Database migrations**: do not exist. No migrations.
- **CI/CD workflows**: no changes (spec-only modification).
- **`docs/drafts/ideas.md`**: line 12 references "group-role mappings"
  as an implemented idea → already crossed out, no change needed.
- **`docs/drafts/open-points.md`**: line 85-117 discusses SSO rate
  limiting → remains valid for the deferred SSO spec, no change needed.
