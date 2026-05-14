# Audit Trail Redesign

> **Status**: DRAFT — all changes described in this document MUST be reviewed
> and approved together before any modification is applied to specifications,
> data model, or conventions. Do not implement any part of this draft
> independently.

## Motivation

Sentinel currently has two database-level audit trail mechanisms:

- **TicketEvent** — comprehensive (24 event types), covers all ticket
  mutations
- **FetcherAuditLog** — lightweight (4 action types), covers admin actions
  on fetchers

All other auditable operations (user lifecycle, role changes, API keys,
role mappings, system settings) rely on INFO-level application logging,
which is volatile, not queryable via API, and unsuitable for compliance
auditing. This draft proposes a systematic redesign to fill those gaps and
establish a consistent audit trail architecture across the platform.

## Design Decisions

### Approach: domain-specific tables (Option A)

Each audit trail has its own database table with a domain-specific enum.
This is consistent with the existing pattern (TicketEvent and
FetcherAuditLog are separate tables with separate enums). Rationale:
type-safe schemas, domain-specific queries, no catch-all blob table.

### Organization of specifications

| Component | Location | Content |
|---|---|---|
| Common conventions + index | `docs/conventions.md`, section "Audit Trail Conventions" | Process rules, BaseAuditLog reference, audit trail index |
| BaseAuditLog class | `backend/app/services/base_audit_log.py` | Common fields, registry, retention, helper |
| Ticket audit log | `docs/features/tickets/ticket-audit-log.md` (renamed) | TicketEvent contract, API, service rules |
| Identity audit log | `docs/features/identity/identity-audit-log.md` (new) | User lifecycle, roles, API keys, role mappings |
| Setting audit log | `docs/features/platform/admin.md`, new section (new) | System setting modifications |
| Fetcher audit log | `docs/features/platform/fetcher-infrastructure.md` (unchanged) | FetcherAuditLog — no changes needed |

### BaseAuditLog — lightweight base class

Every audit trail implementation MUST inherit from `BaseAuditLog`, a
lightweight base class that provides structural consistency across all
audit trails. This is not a heavy framework like `BaseFetcher` — it
enforces a common pattern without orchestrating complex lifecycles.

**Location**: `backend/app/services/base_audit_log.py`

**Responsibilities**:

1. **Common field enforcement**: every subclass inherits the mandatory
   fields (`id`, `created_at`, actor reference)
2. **Auto-registration**: a registry of all audit trail implementations,
   populated via `__init_subclass__`. Useful for the admin overview
   endpoint and future retention tasks
3. **Retention declaration**: `default_retention_days: int | None` where
   `None` means indefinite retention (no expiration). Each subclass can
   override the default
4. **Event creation helper**: a `log_event()` class method that creates
   an audit record within the current database transaction, enforcing
   atomicity

**Abstract interface**:

```python
class BaseAuditLog:
    """Base for all audit trail implementations."""

    # Subclass MUST define:
    name: str                          # e.g., "ticket", "identity", "setting"
    description: str                   # human-readable purpose
    model_class: type                  # SQLAlchemy model (e.g., TicketEvent)
    default_retention_days: int | None = None  # None = indefinite

    # Auto-registration in global registry (populated by __init_subclass__)

    @classmethod
    async def log_event(cls, session: AsyncSession, **kwargs) -> None:
        """Create an audit record in the current transaction.

        Subclasses may override to add domain-specific validation.
        """
        ...
```

**Concrete subclasses**:

```python
class TicketAuditLog(BaseAuditLog):
    name = "ticket"
    description = "Ticket lifecycle and mutation events"
    model_class = TicketEvent
    default_retention_days = None  # indefinite


class IdentityAuditLog(BaseAuditLog):
    name = "identity"
    description = "User lifecycle, roles, API keys, and role mappings"
    model_class = IdentityAuditEvent
    default_retention_days = None  # indefinite


class SettingAuditLog(BaseAuditLog):
    name = "setting"
    description = "System setting modifications"
    model_class = SettingAuditEvent
    default_retention_days = None  # indefinite


class FetcherAuditLog(BaseAuditLog):
    name = "fetcher"
    description = "Administrative actions on fetchers"
    model_class = FetcherAuditLogEntry
    default_retention_days = None  # indefinite
```

### Retention policy

Default retention is **indefinite** (`None`). Each subclass can override
`default_retention_days` with an integer value. If in the future a
runtime-configurable retention is needed, a `SystemSetting` can be
introduced — but this is deferred (YAGNI).

A future cleanup task could iterate the `BaseAuditLog` registry and
delete records older than `default_retention_days` for each trail where
the value is not `None`.

---

## Change 1: Conventions — Audit Trail Conventions section

**Target file**: `docs/conventions.md`

**Action**: add a new top-level section "Audit Trail Conventions" with the
following content.

### Audit Trail Conventions

#### BaseAuditLog class

Every audit trail in Sentinel MUST be implemented as a subclass of
`BaseAuditLog` (`backend/app/services/base_audit_log.py`). The base class
defines:

- **Mandatory fields**: `id` (UUID PK), `created_at` (TIMESTAMP, database
  default), actor field (FK to User, nullability determined by the
  subclass — nullable when system actions are possible, NOT NULL when
  only human actions are recorded)
- **Auto-registration**: all subclasses are automatically registered in a
  global registry, keyed by `name`
- **Retention**: `default_retention_days: int | None` — `None` means
  indefinite retention. Subclasses override as needed
- **Event creation**: `log_event()` class method inserts a record within
  the caller's database transaction

#### Naming

| Element | Pattern | Example |
|---|---|---|
| Database table | `{domain}_audit_event` or legacy name | `identity_audit_event`, `ticket_event` |
| SQLAlchemy model | `{Domain}AuditEvent` or legacy name | `IdentityAuditEvent`, `TicketEvent` |
| Enum | `{Domain}AuditEventType` or legacy name | `IdentityAuditEventType`, `TicketEventType` |
| BaseAuditLog subclass | `{Domain}AuditLog` | `IdentityAuditLog`, `TicketAuditLog` |
| Spec file (standalone) | `{domain}-audit-log.md` | `identity-audit-log.md`, `ticket-audit-log.md` |

Legacy names (TicketEvent, FetcherAuditLog) are preserved to avoid
unnecessary churn; new audit trails follow the standard pattern.

#### Atomicity

Every audit event MUST be created in the same database transaction as the
mutation it records. If the mutation is rolled back, the audit event must
not persist. This is enforced by using `BaseAuditLog.log_event()` with the
same `AsyncSession` as the mutation.

#### Actor field

- `user_id` is set when the action was initiated by a human user
- `user_id` is `NULL` when the action was initiated by the system (e.g.,
  background task, AD sync, automated detection)
- Audit trails where only human actions are possible (e.g.,
  FetcherAuditLog — only admins can act) use NOT NULL for the actor field

#### Audit Trail Index

When adding a new audit trail, update this index.

| Audit Trail | Table | Event Types | Retention | Owning Spec |
|---|---|---|---|---|
| Ticket | `ticket_event` | 24 | Indefinite | `docs/features/tickets/ticket-audit-log.md` |
| Fetcher | `fetcher_audit_log` | 4 | Indefinite | `docs/features/platform/fetcher-infrastructure.md` |
| Identity | `identity_audit_event` | 12 | Indefinite | `docs/features/identity/identity-audit-log.md` |
| Setting | `setting_audit_event` | 1 | Indefinite | `docs/features/platform/admin.md` |

---

## Change 2: Rename ticket-history.md to ticket-audit-log.md

**Target file**: `docs/features/tickets/ticket-history.md`

**Actions**:

1. Rename the file to `docs/features/tickets/ticket-audit-log.md`
2. Update the document title from "Ticket History" to "Ticket Audit Log"
3. Move the Frontend section (filter bar, event timeline, icon mapping,
   description templates, pagination, empty state — current lines 150-280)
   to `docs/features/ui/pages/ticket-detail.md`, expanding the existing
   "Event History (Tab)" section (current lines 215-226) with the full
   UI specification
4. Add a data retention statement: "Retention: indefinite. TicketEvent
   records are never automatically deleted."
5. Add a reference to `BaseAuditLog`: "The `TicketAuditLog` subclass of
   `BaseAuditLog` provides the event creation helper and registers this
   audit trail in the global registry."
6. Update cross-references section to include `docs/conventions.md`
   (Audit Trail Conventions)

**Cross-reference updates required** (files that reference
`ticket-history.md`):

The following files contain references to `ticket-history.md` and must be
updated to point to `ticket-audit-log.md`:

- `AGENTS.md` (Guardrail 11)
- `docs/data-model.md`
- `docs/features/tickets/tickets.md`
- `docs/features/tickets/cvss-scoring.md`
- `docs/features/packages/package-tracking.md`
- `docs/features/packages/product-lifecycle-transitions.md`
- `docs/features/identity/user-service.md`
- `docs/features/ui/pages/ticket-detail.md`
- Any other file found via grep at application time

---

## Change 3: New spec — Identity Audit Log

**Target file**: `docs/features/identity/identity-audit-log.md` (new)

### Purpose

Provide a persistent, queryable audit trail for all identity-related
operations in Sentinel: user lifecycle, role assignments, API key
management, and role mapping administration.

This audit trail replaces the INFO-level application logging currently
specified for these operations. Application-level logging MAY be retained
alongside the database audit trail for operational monitoring, but the
database record is the authoritative audit source.

### Data Model

#### IdentityAuditEvent table

| Column | Type | Constraints | Description |
|---|---|---|---|
| id | UUID | PK | Internal identifier |
| event_type | ENUM | NOT NULL | See IdentityAuditEventType |
| user_id | UUID | FK(user.id), nullable | Admin/user who performed the action. NULL for system actions (AD sync, auto-lock) |
| target_user_id | UUID | FK(user.id), nullable | The user affected by the action. NULL for role mapping events (which affect configuration, not a specific user) |
| old_value | VARCHAR | nullable | Previous state (human-readable) |
| new_value | VARCHAR | nullable | New state (human-readable) |
| detail | JSONB | nullable | Additional structured context when old_value/new_value are insufficient |
| created_at | TIMESTAMP | NOT NULL, DEFAULT | When the event occurred |

**Notes**:

- `target_user_id` distinguishes "who acted" from "who was affected". For
  example, when an admin resets another user's password: `user_id` = admin,
  `target_user_id` = target user
- For role mapping events, `target_user_id` is NULL because the action
  affects a configuration rule, not a specific user. The `detail` JSONB
  captures the mapping details and affected user count
- `detail` JSONB is used for structured data that does not fit the
  old_value/new_value pattern (e.g., role mapping metadata, affected
  user counts)

#### IdentityAuditEventType enum

| Value | Trigger | `user_id` | `target_user_id` | `old_value` | `new_value` | `detail` |
|---|---|---|---|---|---|---|
| `user_created` | Manual user creation (not AD sync) | Creating admin | Created user | `NULL` | Username | `NULL` |
| `user_deactivated` | Admin or AD sync deactivation | Admin for manual, `NULL` for AD sync | Deactivated user | `active` | `inactive` | Reason (e.g., `{"reason": "ad_sync_missing"}` or `{"reason": "admin_action"}`) |
| `user_reactivated` | Admin reactivation | Admin | Reactivated user | `inactive` | `active` | `NULL` |
| `user_locked` | Failed password threshold exceeded | `NULL` (system) | Locked user | `NULL` | `locked` | `{"failed_attempts": N}` |
| `user_unlocked` | Admin unlocks user | Admin | Unlocked user | `locked` | `NULL` | `NULL` |
| `password_reset` | Admin resets another user's password | Admin | Target user | `NULL` | `NULL` | `NULL` |
| `role_added` | Admin or AD sync adds role | Admin for manual, `NULL` for AD sync | Target user | `NULL` | Role name (e.g., `admin`) | For AD sync: `{"source": "ad_sync", "mapping": "cn=SecurityTeam"}` |
| `role_removed` | Admin or AD sync removes role | Admin for manual, `NULL` for AD sync | Target user | Role name (e.g., `admin`) | `NULL` | For AD sync: `{"source": "ad_sync", "mapping": "cn=SecurityTeam"}` |
| `role_mapping_created` | Admin creates AD group-to-role mapping | Admin | `NULL` | `NULL` | `"{ad_group} -> {role}"` | `{"ad_group_cn": "...", "role": "...", "affected_users": N}` |
| `role_mapping_deleted` | Admin deletes AD group-to-role mapping | Admin | `NULL` | `"{ad_group} -> {role}"` | `NULL` | `{"ad_group_cn": "...", "role": "...", "affected_users": N}` |
| `api_key_created` | User or admin creates API key | Acting user | Key owner | `NULL` | Key name/label | `{"key_id": "uuid"}` |
| `api_key_revoked` | User or admin revokes API key | Acting user | Key owner | Key name/label | `NULL` | `{"key_id": "uuid"}` |

### Spec updates required

The following specs currently prescribe INFO-level application logging for
these operations. Each must be updated to reference the
`IdentityAuditLog` instead:

| Spec | Lines | Operation | Current logging |
|---|---|---|---|
| `docs/features/identity/user-service.md` | 320-321 | Role changes | INFO log |
| `docs/features/identity/user-service.md` | 364 | Role mapping sync | INFO log |
| `docs/features/identity/user-service.md` | 401 | Role mapping deletion | INFO log |
| `docs/features/identity/user-service.md` | 468-469 | User deactivation | INFO log |
| `docs/features/identity/user-service.md` | 497-498 | User reactivation | INFO log |
| `docs/features/identity/user-service.md` | 566 | User unlock | INFO log |
| `docs/features/identity/user-management.md` | 852-853 | Admin password reset | INFO log |
| `docs/features/identity/ad-integration.md` | 770-773 | Role mapping creation | INFO log (JSON) |
| `docs/features/identity/ad-integration.md` | 806-809 | Role mapping deletion | INFO log (JSON) |
| `docs/features/identity/user-management.md` | 1093-1097 | Audit trail summary | References INFO logs |

Each of these locations must be updated to:
1. Create an `IdentityAuditEvent` record (via the `IdentityAuditLog`
   helper) in the same transaction as the mutation
2. Optionally retain the INFO-level log line for operational monitoring

### API

#### List Identity Audit Events

```
GET /api/v1/admin/identity-audit-log
```

Returns a paginated list of identity audit events, ordered by
`created_at` descending.

**Query parameters**:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `page` | int | 1 | Page number (1-indexed) |
| `per_page` | int | 20 | Items per page (max 100) |
| `event_type` | string | -- | Comma-separated list of event types |
| `actor` | string | -- | Filter by actor: user UUID, or `system` for automated events |
| `target_user` | string | -- | Filter by target user (UUID or username) |

**Permissions**: Admin role required.

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
        "full_name": "John Doe"
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

### UI

The identity audit log is displayed in the Admin panel as a dedicated
section or tab. The specific UI layout will be defined in
`docs/features/ui/pages/admin-settings.md` when the admin panel UI is
specified in detail.

### Service Contract

Every service function that modifies identity-related data (user
lifecycle, roles, API keys, role mappings) MUST create an
`IdentityAuditEvent` via `IdentityAuditLog.log_event()` in the same
database transaction as the mutation.

### Data Retention

Indefinite. IdentityAuditEvent records are never automatically deleted.

### Testing Requirements

Tests for any identity-mutating service MUST verify:

1. An `IdentityAuditEvent` record is created after the operation
2. The `event_type` matches the expected value
3. `user_id` and `target_user_id` are correctly populated
4. `old_value`, `new_value`, and `detail` are correctly populated
5. The event is created in the same transaction (rollback = no event)

### Cross-references

- `docs/conventions.md` — Audit Trail Conventions
- `docs/api-spec.md` — global API conventions
- `docs/features/identity/user-service.md` — service operations that
  produce identity audit events
- `docs/features/identity/ad-integration.md` — AD sync operations that
  produce identity audit events
- `docs/features/identity/rbac.md` — Endpoint Permission Map (add new
  endpoint)

---

## Change 4: Setting Audit Log — new section in admin.md

**Target file**: `docs/features/platform/admin.md`

**Action**: add a new section "Setting Audit Log" with the following
content.

### Setting Audit Log

Every modification to a system setting MUST produce a
`SettingAuditEvent` record in the same database transaction as the
setting update.

#### SettingAuditEvent table

| Column | Type | Constraints | Description |
|---|---|---|---|
| id | UUID | PK | Internal identifier |
| event_type | ENUM | NOT NULL | See SettingAuditEventType |
| setting_key | VARCHAR | NOT NULL | Which setting was changed |
| user_id | UUID | FK(user.id), NOT NULL | Admin who changed the setting |
| old_value | VARCHAR | nullable | Previous value |
| new_value | VARCHAR | NOT NULL | New value |
| created_at | TIMESTAMP | NOT NULL, DEFAULT | When the change occurred |

**Notes**:

- `user_id` is NOT NULL because only admins can modify settings (no
  system-initiated changes)
- `setting_key` identifies which setting was changed (e.g.,
  `default_cvss_version`)

#### SettingAuditEventType enum

| Value | Trigger | Description |
|---|---|---|
| `setting_changed` | Admin modifies a system setting | Captures old and new value |

A single event type is sufficient for now. If future settings require
distinct event types (e.g., a setting that triggers side effects vs one
that does not), the enum can be extended.

#### API

```
GET /api/v1/admin/settings/audit-log
```

Returns a paginated list of setting changes, ordered by `created_at`
descending.

**Query parameters**:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `page` | int | 1 | Page number (1-indexed) |
| `per_page` | int | 20 | Items per page (max 100) |
| `setting_key` | string | -- | Filter by setting key |

**Permissions**: Admin role required.

**Response** (200 OK):

```json
{
  "data": [
    {
      "id": "uuid",
      "event_type": "setting_changed",
      "setting_key": "default_cvss_version",
      "old_value": "3.1",
      "new_value": "4.0",
      "created_at": "2026-05-13T14:00:00Z",
      "actor": {
        "id": "uuid",
        "username": "asmith",
        "full_name": "Alice Smith"
      }
    }
  ],
  "meta": {
    "total": 3,
    "page": 1,
    "per_page": 20
  }
}
```

**Error responses**:

| Status | Code | Condition |
|---|---|---|
| 403 | `AUTH_INSUFFICIENT_ROLE` | Caller does not have Admin role |

#### UI

The setting audit log is displayed in the Admin Settings page
(`/admin/settings`) below the settings form, as a collapsible section
showing the history of changes.

#### Data Retention

Indefinite. SettingAuditEvent records are never automatically deleted.

---

## Change 5: Data model updates

**Target file**: `docs/data-model.md`

**Actions**:

1. Add `IdentityAuditEvent` table and `IdentityAuditEventType` enum
2. Add `SettingAuditEvent` table and `SettingAuditEventType` enum
3. Add a note referencing `BaseAuditLog` and the Audit Trail Conventions
   in `conventions.md`

---

## Change 6: Cross-reference updates

### Files referencing ticket-history.md (rename to ticket-audit-log.md)

All references to `ticket-history.md` must be updated to
`ticket-audit-log.md`. Known locations:

- `AGENTS.md` (Guardrail 11)
- `docs/data-model.md`
- `docs/features/tickets/tickets.md`
- `docs/features/tickets/cvss-scoring.md`
- `docs/features/packages/package-tracking.md`
- `docs/features/packages/product-lifecycle-transitions.md`
- `docs/features/identity/user-service.md`
- `docs/features/ui/pages/ticket-detail.md`

### Endpoint Permission Map (rbac.md)

Add new endpoints:

| Method | Path | Access Level | Owning Spec |
|---|---|---|---|
| GET | `/api/v1/admin/identity-audit-log` | Admin | `identity/identity-audit-log.md` |
| GET | `/api/v1/admin/settings/audit-log` | Admin | `platform/admin.md` |

### Identity specs (replace app logging with audit trail)

The specs listed in Change 3 "Spec updates required" must be updated to
reference the `IdentityAuditLog` instead of (or in addition to)
INFO-level logging.

---

## Change 7: FetcherAuditLog alignment

No changes to the FetcherAuditLog specification or data model are
required. The existing `FetcherAuditLog` will be registered in the
`BaseAuditLog` registry by creating a `FetcherAuditLog(BaseAuditLog)`
subclass at implementation time.

The only update is adding FetcherAuditLog to the Audit Trail Index in
`conventions.md` (already included in Change 1).

---

## Excluded from scope

The following were considered and explicitly excluded:

| Item | Reason |
|---|---|
| Session events (login/logout) | App-level logging is sufficient; sessions are cleaned up weekly and the audit value is low compared to the volume |
| User creation via AD sync | AD is the source of truth; bulk-logging hundreds of `user_created` events on first sync adds noise without value |
| Product lifecycle changes (SMELT/AIMAAS) | External systems are the source of truth |
| SubmissionRequest / ReleaseRequest state changes | IBS is the source of truth |
| Runtime-configurable retention via SystemSetting | YAGNI — can be added later if needed; the `BaseAuditLog.default_retention_days` attribute provides the extension point |

---

## Implementation order (suggested)

1. `BaseAuditLog` base class
2. Conventions section in `conventions.md`
3. Rename `ticket-history.md` and refactor (TicketAuditLog subclass)
4. `IdentityAuditLog` + spec updates
5. `SettingAuditLog` + admin.md updates
6. FetcherAuditLog subclass registration
7. Data model updates
8. Cross-reference updates
9. Endpoint Permission Map updates

---

## Open questions

- None at this time. All design decisions have been discussed and agreed.
