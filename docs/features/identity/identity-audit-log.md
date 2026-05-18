# Identity Audit Log

## Purpose

Provide a persistent, queryable audit trail for all identity-related
operations in Sentinel: user lifecycle, role assignments, API key
management, and role mapping administration.

This audit trail replaces the INFO-level application logging currently
specified for these operations. Application-level logging MAY be retained
alongside the database audit trail for operational monitoring, but the
database record is the authoritative audit source.

## Data Model

### IdentityAuditEvent Table

Inherits `id`, `created_at`, and `user_id` from `AuditEventMixin`.

| Column | Type | Constraints | Description |
|---|---|---|---|
| id | UUID | PK | Inherited from AuditEventMixin |
| event_type | ENUM | NOT NULL | See IdentityAuditEventType |
| user_id | UUID | FK(user.id), nullable | Inherited from AuditEventMixin. Admin/user who performed the action. NULL for system actions (AD sync) |
| target_user_id | UUID | FK(user.id), nullable | The user affected by the action. NULL for role mapping events (which affect configuration, not a specific user) |
| old_value | TEXT | nullable | Previous state (human-readable) |
| new_value | TEXT | nullable | New state (human-readable) |
| detail | JSONB | nullable | Additional structured context when old_value/new_value are insufficient |
| created_at | TIMESTAMPTZ | NOT NULL, DEFAULT | Inherited from AuditEventMixin |

**Notes**:

- `id`, `created_at`, and `user_id` are inherited from `AuditEventMixin`
- `target_user_id` distinguishes "who acted" from "who was affected". For
  example, when an admin resets another user's password: `user_id` = admin,
  `target_user_id` = target user
- For role mapping events, `target_user_id` is NULL because the action
  affects a configuration rule, not a specific user. The `detail` JSONB
  captures the mapping details and affected user count
- `detail` JSONB is used for structured data that does not fit the
  old_value/new_value pattern (e.g., role mapping metadata, affected
  user counts)
- `old_value` and `new_value` must not exceed 512 characters. This limit
  is derived from the constraints of the source columns: username max 64
  characters, email max 255 characters, role names and status strings are
  shorter still. The service layer must silently truncate any value
  exceeding this limit before writing the audit event

### IdentityAuditEventType Enum

| Value | Trigger | `user_id` | `target_user_id` | `old_value` | `new_value` | `detail` |
|---|---|---|---|---|---|---|
| `user_created` | User account created (manual or AD sync) | Creating admin for manual, `NULL` for AD sync | Created user | `NULL` | Username | `NULL` |
| `user_deactivated` | Admin or AD sync deactivation | Admin for manual, `NULL` for AD sync | Deactivated user | `active` | `inactive` | Reason (e.g., `{"reason": "ad_sync_missing"}` or `{"reason": "admin_action"}`) |
| `user_reactivated` | Admin reactivation | Admin | Reactivated user | `inactive` | `active` | `NULL` |
| `password_reset` | Admin resets another user's password | Admin | Target user | `NULL` | `NULL` | `NULL` |
| `role_added` | Admin or AD sync adds role | Admin for manual, `NULL` for AD sync | Target user | `NULL` | Role name (e.g., `admin`) | For AD sync: `{"source": "ad_sync", "mapping": "cn=SecurityTeam"}` |
| `role_removed` | Admin or AD sync removes role | Admin for manual, `NULL` for AD sync | Target user | Role name (e.g., `admin`) | `NULL` | For AD sync: `{"source": "ad_sync", "mapping": "cn=SecurityTeam"}` |
| `role_mapping_created` | Admin creates AD group-to-role mapping | Admin | `NULL` | `NULL` | `"{ad_group} -> {role}"` | `{"ad_group_cn": "...", "role": "...", "affected_users": N}` |
| `role_mapping_deleted` | Admin deletes AD group-to-role mapping | Admin | `NULL` | `"{ad_group} -> {role}"` | `NULL` | `{"ad_group_cn": "...", "role": "...", "affected_users": N}` |
| `username_changed` | AD sync detects sAMAccountName change for existing user (matched via objectGUID) | `NULL` (system) | Renamed user | Old username | New username | `NULL` |
| `api_key_created` | User or admin creates API key | Acting user | Key owner | `NULL` | Key name/label | `{"key_id": "uuid"}` |
| `api_key_revoked` | User, admin, or system revokes API key | Acting user or `NULL` (system) | Key owner | Key name/label | `NULL` | `{"key_id": "uuid", "reason": "user_deactivated"}` (reason only for bulk revocation during deactivation) |
| `email_changed` | Email address updated (admin or AD sync) | Admin for manual, `NULL` for AD sync | Target user | Old email | New email | `NULL` |
| `full_name_changed` | Full name updated (admin or AD sync) | Admin for manual, `NULL` for AD sync | Target user | Old full name | New full name | `NULL` |
| `manager_changed` | Direct manager updated (AD sync) | `NULL` (system) | Target user | Old manager username (or `NULL`) | New manager username (or `NULL`) | `NULL` |

### detail JSONB Schema Contract

The `detail` column carries structured context for event types where
`old_value`/`new_value` are insufficient. Every event type that
populates `detail` MUST have its schema defined in the table below.
Event types not listed here MUST set `detail` to `NULL`.

| Event Type | Required Keys | Optional Keys | Example |
|---|---|---|---|
| `user_deactivated` | `reason` (string) | — | `{"reason": "ad_sync_missing"}` |
| `role_added` | — | `source` (string), `mapping` (string) | `{"source": "ad_sync", "mapping": "cn=SecurityTeam"}` |
| `role_removed` | — | `source` (string), `mapping` (string) | `{"source": "ad_sync", "mapping": "cn=SecurityTeam"}` |
| `role_mapping_created` | `ad_group_cn` (string), `role` (string), `affected_users` (int) | — | `{"ad_group_cn": "cn=SecurityTeam", "role": "admin", "affected_users": 5}` |
| `role_mapping_deleted` | `ad_group_cn` (string), `role` (string), `affected_users` (int) | — | `{"ad_group_cn": "cn=SecurityTeam", "role": "admin", "affected_users": 3}` |
| `api_key_created` | `key_id` (UUID string) | — | `{"key_id": "550e8400-e29b-41d4-a716-446655440000"}` |
| `api_key_revoked` | `key_id` (UUID string) | `reason` (string) | `{"key_id": "550e8400-e29b-41d4-a716-446655440000", "reason": "user_deactivated"}` |

**Notes**:

- `role_added` and `role_removed`: `detail` is `NULL` for manual admin
  actions. The optional keys (`source`, `mapping`) are present only when
  the role change originates from AD sync. When `detail` is non-NULL,
  both `source` and `mapping` MUST be present together
- `api_key_revoked`: the `reason` key is present only for bulk
  revocations triggered by user deactivation. For individual manual
  revocations, `detail` contains only `key_id`
- Maximum payload size: 4 KB. The service layer MUST reject any
  `detail` value exceeding this limit
- The service layer MUST validate that `detail` contains only keys
  defined in this contract for the given event type — undocumented keys
  are rejected
- When a new `IdentityAuditEventType` is added that uses the `detail`
  column, this table MUST be extended with the corresponding schema
  definition before the implementation proceeds

## API

### List Identity Audit Events

```
GET /api/v1/admin/identity/audit-log
```

Returns a paginated list of identity audit events, ordered by
`created_at` descending. Sorting is fixed — client-controlled
`sort_by` / `sort_order` parameters are not supported (audit trail
entries are always displayed in reverse chronological order).

**Query parameters**:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `page` | int | 1 | Page number (1-indexed) |
| `per_page` | int | 20 | Items per page (max 100) |
| `event_type` | string | -- | Comma-separated list of event types. See `docs/api-spec.md` (Enum Filter Validation) for handling of invalid values |
| `actor` | string | -- | Filter by actor: user UUID, username, or `system` for automated events |
| `target_user` | string | -- | Filter by target user (UUID or username) |
| `from_date` | string | -- | ISO 8601 date/datetime. Include events from this date onwards (inclusive) |
| `to_date` | string | -- | ISO 8601 date/datetime. Include events up to this date (inclusive) |

**Permissions**: Admin role required. For non-admin users, a self-scoped
endpoint is available at `GET /api/v1/users/me/audit-log` (see below).

**Response** (200 OK):

```json
{
  "data": [
    {
      "id": "uuid",
      "event_type": "role_added",
      "old_value": null,
      "new_value": "admin",
      "detail": {"source": "ad_sync", "mapping": "cn=SecurityTeam"},
      "created_at": "2026-05-13T10:30:00Z",
      "actor": null,
      "target_user": {
        "id": "uuid",
        "username": "jdoe",
        "full_name": "John Doe",
        "active": true
      }
    }
  ],
  "meta": {
    "total": 156,
    "page": 1,
    "per_page": 20
  }
}
```

**Error responses**:

| Status | Code | Condition |
|---|---|---|
| 403 | `AUTH_INSUFFICIENT_ROLE` | Caller does not have Admin role |

### List My Identity Audit Events

```
GET /api/v1/users/me/audit-log
```

Returns a paginated list of identity audit events where the
authenticated user is the target (`target_user_id = current_user.id`).
The target filter is implicit and not exposed as a query parameter.
Events with `target_user_id IS NULL` (role mapping configuration
events) are excluded.

Sorting is fixed at `created_at` descending — client-controlled
`sort_by` / `sort_order` parameters are not supported.

**Query parameters**:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `page` | int | 1 | Page number (1-indexed) |
| `per_page` | int | 20 | Items per page (max 100) |
| `event_type` | string | -- | Comma-separated list of event types. See `docs/api-spec.md` (Enum Filter Validation) for handling of invalid values |
| `from_date` | string | -- | ISO 8601 date/datetime. Include events from this date onwards (inclusive) |
| `to_date` | string | -- | ISO 8601 date/datetime. Include events up to this date (inclusive) |

The `actor` and `target_user` filters are not available on this
endpoint. The target is always the authenticated user; the actor is
anonymized in the response (see below).

**Permissions**: Authenticated.

**Response** (200 OK):

```json
{
  "data": [
    {
      "id": "uuid",
      "event_type": "role_added",
      "old_value": null,
      "new_value": "admin",
      "detail": {"source": "ad_sync", "mapping": "cn=SecurityTeam"},
      "created_at": "2026-05-13T10:30:00Z",
      "actor": "system"
    }
  ],
  "meta": {
    "total": 42,
    "page": 1,
    "per_page": 20
  }
}
```

**Actor anonymization**: the `actor` field is returned as a string
(not an object) to prevent non-admin users from identifying which
specific administrator performed an action. The mapping is:

| DB condition | `actor` value |
|---|---|
| `user_id IS NULL` | `"system"` |
| `user_id = target_user_id` | `"self"` |
| `user_id IS NOT NULL AND user_id ≠ target_user_id` | `"admin"` |

This gives users visibility into whether an event was triggered by
themselves, by an administrator, or by an automated system process,
without exposing the administrator's identity.

**`detail` field transparency**: the `detail` JSONB field is returned
unredacted in the self-service response. This includes AD group CNs in
`detail.mapping` for AD sync events (e.g.,
`{"source": "ad_sync", "mapping": "cn=SecurityTeam"}`). AD group CNs
are considered non-sensitive organizational metadata — they are
meaningful only within the AD administrative context and do not
constitute personal data or security-critical information.

**Error responses**:

| Status | Code | Condition |
|---|---|---|
| 401 | `AUTH_NOT_AUTHENTICATED` | Caller is not authenticated |

## UI

The identity audit log is displayed in two contexts:

- **Admin panel**: full audit log with all events and unmasked actor
  identity. The specific UI layout will be defined in
  `docs/features/ui/pages/admin-settings.md` when the admin panel UI is
  specified in detail.
- **User profile**: self-scoped audit log showing only events that
  affect the current user, with anonymized actor. The specific UI layout
  will be defined when the user profile page is specified in detail.

## Service Contract

Every service function that modifies identity-related data (user
lifecycle, roles, API keys, role mappings) MUST create an
`IdentityAuditEvent` via `IdentityAuditLog.log_event()` in the same
database transaction as the mutation.

**API key audit events**: the `api_key_service` is the centralized
location for all API key mutations. Audit events are created inside the
service functions, not in the calling endpoints:

- `create_key()` → creates 1 `api_key_created` event
- `revoke_key()` → creates 1 `api_key_revoked` event
- `revoke_all_user_keys()` → creates N `api_key_revoked` events (one per
  revoked key). Each event includes `{"reason": "user_deactivated"}` in
  the `detail` JSONB field to distinguish bulk revocation during
  deactivation from individual manual revocations

Session invalidation during deactivation does NOT produce audit events
(sessions are excluded from the audit trail scope).

**Field-change events**: the `user_service.update_user()` function
produces one audit event per changed field. If a single `update_user()`
call modifies both `email` and `full_name`, two events are created
(`email_changed` + `full_name_changed`) in the same transaction.

**AD sync coverage**: the `user_created` event type applies to ALL user
creation regardless of source (manual admin creation AND AD sync). On
initial AD sync this may produce hundreds of `user_created` events —
this is intentional to maintain a complete, coherent history for every
user. Field-change events (`email_changed`, `full_name_changed`,
`manager_changed`, `username_changed`) are likewise produced by AD sync
when the corresponding fields change in Active Directory.

**Future fields**: if a new mutable field is added to the User table in
the future, a corresponding `{field}_changed` event type MUST be added
to `IdentityAuditEventType`. This is expected to be rare.

## Data Retention

Indefinite. IdentityAuditEvent records are never automatically deleted.

## Testing Requirements

Tests for any identity-mutating service MUST verify:

1. An `IdentityAuditEvent` record is created after the operation
2. The `event_type` matches the expected value
3. `user_id` and `target_user_id` are correctly populated
4. `old_value`, `new_value`, and `detail` are correctly populated
5. The event is created in the same transaction (rollback = no event)

## Cross-references

- `docs/features/platform/audit-trail-infrastructure.md` — BaseAuditLog,
  AuditEventMixin, naming conventions
- `docs/conventions.md` — Audit Trail Conventions
- `docs/api-spec.md` — global API conventions
- `docs/features/identity/user-service.md` — service operations that
  produce identity audit events
- `docs/features/identity/user-management.md` — admin password reset
  and audit trail summary references
- `docs/features/identity/ad-integration.md` — AD sync operations that
  produce identity audit events
- `docs/features/identity/api-key-service.md` — centralized API key
  lifecycle service; produces `api_key_created` and `api_key_revoked`
  events
- `docs/features/identity/rbac.md` — Endpoint Permission Map (add new
  endpoint)
