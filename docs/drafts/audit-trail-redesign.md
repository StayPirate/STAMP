# Audit Trail Redesign

> **Status**: DRAFT — all changes described in this document MUST be reviewed
> and approved together before any modification is applied to specifications,
> data model, or conventions. Do not implement any part of this draft
> independently.

## Motivation

Sentinel currently has two database-level audit trail mechanisms:

- **TicketAuditEvent** — comprehensive (24 event types), covers all ticket
  mutations
- **FetcherAuditEvent** — lightweight (4 action types), covers admin actions
  on fetchers

All other auditable operations (user lifecycle, role changes, API keys,
role mappings, system settings) rely on INFO-level application logging,
which is volatile, not queryable via API, and unsuitable for compliance
auditing. This draft proposes a systematic redesign to fill those gaps and
establish a consistent audit trail architecture across the platform.

## Design Decisions

### Approach: domain-specific tables (Option A)

Each audit trail has its own database table with a domain-specific enum.
This is consistent with the existing pattern (TicketAuditEvent and
FetcherAuditEvent are separate tables with separate enums). Rationale:
type-safe schemas, domain-specific queries, no catch-all blob table.

### Organization of specifications

| Component | Location | Content |
|---|---|---|
| Common conventions + index | `docs/conventions.md`, section "Audit Trail Conventions" | Process rules, BaseAuditLog reference, audit trail index |
| BaseAuditLog class | `backend/app/services/base_audit_log.py` | Common fields, registry, retention, helper |
| Ticket audit log | `docs/features/tickets/ticket-audit-log.md` (renamed) | TicketAuditEvent contract, API, service rules |
| Identity audit log | `docs/features/identity/identity-audit-log.md` (new) | User lifecycle, roles, API keys, role mappings |
| Setting audit log | `docs/features/platform/admin.md`, new section (new) | System setting modifications |
| Fetcher audit log | `docs/features/platform/fetcher-infrastructure.md` | FetcherAuditEvent contract, renames to standard pattern |

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
5. **Standard date filtering**: an `apply_date_filters(query, from_date,
   to_date)` class method that applies `WHERE created_at` filters to a
   SQLAlchemy query. Semantics: `from_date` only → `>= from_date`;
   `to_date` only → `<= to_date`; both → inclusive range; neither → no
   date filter. Parameters accept ISO 8601 date or datetime values.
   Every audit trail API endpoint MUST call this method to ensure
   uniform date filtering behavior

**Abstract interface**:

```python
class BaseAuditLog:
    """Base for all audit trail implementations."""

    # Subclass MUST define:
    name: str                          # e.g., "ticket", "identity", "setting"
    description: str                   # human-readable purpose
    model_class: type                  # SQLAlchemy model (e.g., TicketAuditEvent)
    default_retention_days: int | None = None  # None = indefinite

    # Auto-registration in global registry (populated by __init_subclass__)

    @classmethod
    async def log_event(cls, session: AsyncSession, **kwargs) -> None:
        """Create an audit record in the current transaction.

        Subclasses may override to add domain-specific validation.
        """
        ...

    @classmethod
    def apply_date_filters(
        cls,
        query: Select,
        from_date: date | datetime | None = None,
        to_date: date | datetime | None = None,
    ) -> Select:
        """Apply created_at date range filters to a query.

        - from_date only: WHERE created_at >= from_date
        - to_date only:   WHERE created_at <= to_date
        - both:           WHERE created_at >= from_date
                            AND created_at <= to_date
        - neither:        no filter applied
        """
        ...

    @classmethod
    def filter_by_actor(
        cls,
        query: Select,
        actor: str | None = None,
    ) -> Select:
        """Filter audit events by actor (user_id column).

        - actor is None:     no filter applied
        - actor == "system": WHERE user_id IS NULL
        - actor is a UUID:   WHERE user_id = <uuid>
        - actor is a string: JOIN User, WHERE username = <actor>

        Relies on the uniform user_id column provided by
        AuditEventMixin across all audit event models.
        """
        ...
```

**Concrete subclasses**:

```python
class TicketAuditLog(BaseAuditLog):
    name = "ticket"
    description = "Ticket lifecycle and mutation events"
    model_class = TicketAuditEvent
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
    model_class = FetcherAuditEvent
    default_retention_days = None  # indefinite
```

**Note**: these subclasses define only service-layer attributes (name,
description, model reference, retention). Database columns (`id`,
`created_at`, `user_id`, and domain-specific columns) are defined in the
SQLAlchemy models pointed to by `model_class`, which inherit from
`AuditEventMixin` for the common columns.

### Retention policy

Default retention is **indefinite** (`None`). Each subclass can override
`default_retention_days` with an integer value. If in the future a
runtime-configurable retention is needed, a `SystemSetting` can be
introduced — but this is deferred (YAGNI).

A future cleanup task could iterate the `BaseAuditLog` registry and
delete records older than `default_retention_days` for each trail where
the value is not `None`.

### AuditEventMixin — shared SQLAlchemy columns

Every audit event model MUST inherit from `AuditEventMixin`, a SQLAlchemy
mixin class that provides the columns common to all audit trail tables.
This is the **model-layer** companion to the service-layer `BaseAuditLog`.

**Location**: `backend/app/models/mixins.py`

**Columns provided**:

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | UUID | PK | Internal identifier |
| `created_at` | TIMESTAMP | NOT NULL, server default | When the event occurred |
| `user_id` | UUID | FK(user.id), nullable | Actor who performed the action. NULL for system-initiated actions |

**Nullability of `user_id`**: the mixin defines `user_id` as nullable at
the database level for all audit event models. Subclasses of
`BaseAuditLog` that only record human-initiated actions may override
`log_event()` to validate that `user_id` is always provided.

**Relationship to BaseAuditLog**:

```
Service layer (behavior)               Model layer (data structure)
─────────────────────────               ────────────────────────────
BaseAuditLog                            AuditEventMixin (id, created_at, user_id)
  │  - log_event()                          │
  │  - apply_date_filters()                 │
  │  - filter_by_actor()                    │
  │  - registry                             │
  │                                         │
  ├── TicketAuditLog ──model_class──▶  TicketAuditEvent (Base + Mixin)
  ├── IdentityAuditLog ─model_class─▶  IdentityAuditEvent (Base + Mixin)
  ├── SettingAuditLog ──model_class──▶  SettingAuditEvent (Base + Mixin)
  └── FetcherAuditLog ─model_class──▶  FetcherAuditEvent (Base + Mixin)
```

`BaseAuditLog` references the model via `model_class` and provides
behavioral methods. `AuditEventMixin` provides the structural columns.
Together they ensure that all audit trails are both structurally
consistent (same base columns) and behaviorally consistent (same
service-layer interface).

---

## Change 1: Audit Trail Infrastructure spec

**Target file**: `docs/features/platform/audit-trail-infrastructure.md`
(new)

**Action**: create a new specification containing the audit trail
infrastructure: `BaseAuditLog` base class, `AuditEventMixin` mixin,
naming conventions, atomicity rules, actor field semantics, date
filtering convention, and the Audit Trail Index. This is the single
canonical source for all cross-cutting audit trail rules.

Content:

### AuditEventMixin

Every audit event SQLAlchemy model MUST inherit from `AuditEventMixin`
(`backend/app/models/mixins.py`). The mixin provides the columns common
to all audit trail tables:

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | UUID | PK | Internal identifier |
| `created_at` | TIMESTAMP | NOT NULL, server default | When the event occurred |
| `user_id` | UUID | FK(user.id), nullable | Actor. NULL for system-initiated actions |

All audit event models inherit these columns from the mixin and add
their own domain-specific columns (e.g., `ticket_id`, `event_type`,
`target_user_id`).

### BaseAuditLog class

Every audit trail in Sentinel MUST be implemented as a subclass of
`BaseAuditLog` (`backend/app/services/base_audit_log.py`). The base class
defines:

- **Auto-registration**: all subclasses are automatically registered in a
  global registry, keyed by `name`
- **Retention**: `default_retention_days: int | None` — `None` means
  indefinite retention. Subclasses override as needed
- **Event creation**: `log_event()` class method inserts a record within
  the caller's database transaction. Subclasses may override to add
  domain-specific validation (e.g., ensuring `user_id` is provided for
  admin-only trails)
- **Date filtering**: `apply_date_filters()` class method applies
  `from_date` / `to_date` filters on `created_at`. Every audit trail API
  endpoint MUST use this method for uniform date filtering behavior
- **Actor filtering**: `filter_by_actor()` class method filters by
  `user_id` column (inherited from `AuditEventMixin`). Accepts `"system"`
  for NULL actor, a UUID string for direct match, or a username for
  lookup via JOIN. Every audit trail API endpoint with an `actor` filter
  MUST use this method

### Naming

| Element | Pattern | Example |
|---|---|---|
| Database table | `{domain}_audit_event` | `ticket_audit_event`, `identity_audit_event` |
| SQLAlchemy model | `{Domain}AuditEvent` | `TicketAuditEvent`, `IdentityAuditEvent` |
| Enum | `{Domain}AuditEventType` | `TicketAuditEventType`, `IdentityAuditEventType` |
| BaseAuditLog subclass | `{Domain}AuditLog` | `TicketAuditLog`, `IdentityAuditLog` |
| Spec file (standalone) | `{domain}-audit-log.md` | `ticket-audit-log.md`, `identity-audit-log.md` |

Endpoint naming convention: see `docs/api-spec.md` (Audit Trail Endpoint
Naming section).

### Atomicity

Every audit event MUST be created in the same database transaction as the
mutation it records. If the mutation is rolled back, the audit event must
not persist. This is enforced by using `BaseAuditLog.log_event()` with the
same `AsyncSession` as the mutation.

### Actor field

- `user_id` is inherited from `AuditEventMixin` and is nullable at the
  database level in all audit event models
- `user_id` is set when the action was initiated by a human user
- `user_id` is `NULL` when the action was initiated by the system (e.g.,
  background task, AD sync, automated detection)
- Subclasses that only record human-initiated actions may override
  `log_event()` to validate that `user_id` is provided

### Date filtering

Every audit trail API endpoint MUST support `from_date` and `to_date`
query parameters (ISO 8601 date or datetime, both optional, inclusive
bounds). Filtering is provided by the `BaseAuditLog.apply_date_filters()`
class method to ensure uniform behavior across all audit trails:

- `from_date` only → records where `created_at >= from_date`
- `to_date` only → records where `created_at <= to_date`
- Both → records in the inclusive range
- Neither → no date filter applied

### Audit Trail Index

When adding a new audit trail, update this index.

| Audit Trail | Table | Event Types | Retention | Owning Spec |
|---|---|---|---|---|
| Ticket | `ticket_audit_event` | 24 | Indefinite | `docs/features/tickets/ticket-audit-log.md` |
| Fetcher | `fetcher_audit_event` | 4 | Indefinite | `docs/features/platform/fetcher-infrastructure.md` |
| Identity | `identity_audit_event` | 16 | Indefinite | `docs/features/identity/identity-audit-log.md` |
| Setting | `setting_audit_event` | 1 | Indefinite | `docs/features/platform/admin.md` |

### Cross-references

- `docs/conventions.md` — Audit Trail reference paragraph
- `docs/api-spec.md` — `/audit-log` endpoint suffix convention
- `docs/data-model.md` — table definitions for all audit event models

---

## Change 1b: Conventions — Audit Trail reference

**Target file**: `docs/conventions.md`

**Action**: add a brief "Audit Trail" subsection under a suitable
location (e.g., after "SQLAlchemy Conventions") with the following
content:

### Audit Trail

Every audit event SQLAlchemy model MUST inherit from `AuditEventMixin`
(`backend/app/models/mixins.py`). Every audit trail MUST be implemented
as a `BaseAuditLog` subclass
(`backend/app/services/base_audit_log.py`).

See `docs/features/platform/audit-trail-infrastructure.md` for the
full specification: base class interface, mixin columns, naming
conventions, atomicity rules, and the Audit Trail Index.

---

## Change 1c: API spec — Audit Trail Endpoint Naming

**Target file**: `docs/api-spec.md`

**Action**: add a subsection "Audit Trail Endpoint Naming" under the
General Conventions section with the following content:

### Audit Trail Endpoint Naming

Every audit trail retrieval endpoint MUST use the `/audit-log` suffix.
The general pattern is `/{resource-scope}/audit-log`:

- Entity-scoped: `GET /api/v1/tickets/{ticket_id}/audit-log`
- Admin-scoped: `GET /api/v1/admin/identity/audit-log`
- Nested: `GET /api/v1/admin/settings/audit-log`
- Named resource: `GET /api/v1/fetchers/{fetcher_name}/audit-log`

See `docs/features/platform/audit-trail-infrastructure.md` for the full
audit trail specification.

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
4. Add a data retention statement: "Retention: indefinite. TicketAuditEvent
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
| id | UUID | PK | Inherited from AuditEventMixin |
| event_type | ENUM | NOT NULL | See IdentityAuditEventType |
| user_id | UUID | FK(user.id), nullable | Inherited from AuditEventMixin. Admin/user who performed the action. NULL for system actions (AD sync, auto-lock) |
| target_user_id | UUID | FK(user.id), nullable | The user affected by the action. NULL for role mapping events (which affect configuration, not a specific user) |
| old_value | VARCHAR | nullable | Previous state (human-readable) |
| new_value | VARCHAR | nullable | New state (human-readable) |
| detail | JSONB | nullable | Additional structured context when old_value/new_value are insufficient |
| created_at | TIMESTAMP | NOT NULL, DEFAULT | Inherited from AuditEventMixin |

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
| `username_changed` | AD sync detects sAMAccountName change for existing user (matched via objectGUID) | `NULL` (system) | Renamed user | Old username | New username | `NULL` |
| `api_key_created` | User or admin creates API key | Acting user | Key owner | `NULL` | Key name/label | `{"key_id": "uuid"}` |
| `api_key_revoked` | User, admin, or system revokes API key | Acting user or `NULL` (system) | Key owner | Key name/label | `NULL` | `{"key_id": "uuid", "reason": "user_deactivated"}` (reason only for bulk revocation during deactivation) |
| `email_changed` | Email address updated (admin or AD sync) | Admin for manual, `NULL` for AD sync | Target user | Old email | New email | `NULL` |
| `full_name_changed` | Full name updated (admin or AD sync) | Admin for manual, `NULL` for AD sync | Target user | Old full name | New full name | `NULL` |
| `manager_changed` | Direct manager updated (AD sync) | `NULL` (system) | Target user | Old manager username (or `NULL`) | New manager username (or `NULL`) | `NULL` |

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
| `docs/features/identity/ad-integration.md` | 499-500 | Username rename (AD sync) | Fetcher execution log + log entry |
| `docs/features/identity/user-management.md` | 1093-1097 | Audit trail summary | References INFO logs |

Each of these locations must be updated to:
1. Create an `IdentityAuditEvent` record (via the `IdentityAuditLog`
   helper) in the same transaction as the mutation
2. Optionally retain the INFO-level log line for operational monitoring

Additionally, the following specs contain TBD audit placeholders that
must be resolved by this redesign:

| Spec | Lines | Operation | Current state |
|---|---|---|---|
| `docs/features/identity/api-key-service.md` | 104-105 | API key creation | TBD placeholder |
| `docs/features/identity/api-key-service.md` | 139-140 | API key revocation (single) | TBD placeholder |
| `docs/features/identity/api-key-service.md` | 171-174 | API key revocation (bulk) | TBD placeholder |

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

### API

#### List Identity Audit Events

```
GET /api/v1/admin/identity/audit-log
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
| `from_date` | string | -- | ISO 8601 date/datetime. Include events from this date onwards (inclusive) |
| `to_date` | string | -- | ISO 8601 date/datetime. Include events up to this date (inclusive) |

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
- `docs/features/identity/api-key-service.md` — centralized API key
  lifecycle service; produces `api_key_created` and `api_key_revoked`
  events
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
| id | UUID | PK | Inherited from AuditEventMixin |
| event_type | ENUM | NOT NULL | See SettingAuditEventType |
| setting_key | VARCHAR | NOT NULL | Which setting was changed |
| user_id | UUID | FK(user.id), nullable | Inherited from AuditEventMixin. Admin who changed the setting |
| old_value | VARCHAR | nullable | Previous value |
| new_value | VARCHAR | NOT NULL | New value |
| created_at | TIMESTAMP | NOT NULL, DEFAULT | Inherited from AuditEventMixin |

**Notes**:

- `id`, `created_at`, and `user_id` are inherited from `AuditEventMixin`
- `user_id` is always present because only admins can modify settings
  (no system-initiated changes)
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
| `actor` | string | -- | Filter by actor: user UUID, username, or `system` for automated events |
| `from_date` | string | -- | ISO 8601 date/datetime. Include events from this date onwards (inclusive) |
| `to_date` | string | -- | ISO 8601 date/datetime. Include events up to this date (inclusive) |

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

#### Cross-references

- `docs/features/platform/audit-trail-infrastructure.md` — BaseAuditLog,
  AuditEventMixin
- `docs/api-spec.md` — global API conventions

---

## Change 5: Data model updates

**Target file**: `docs/data-model.md`

**Actions**:

1. Add `AuditEventMixin` documentation — describe the shared mixin with
   columns `id`, `created_at`, `user_id` (nullable), and note that all
   audit event models inherit from it
2. Add `IdentityAuditEvent` table and `IdentityAuditEventType` enum
3. Add `SettingAuditEvent` table and `SettingAuditEventType` enum
4. Rename `TicketEvent` to `TicketAuditEvent`, `TicketEventType` to
   `TicketAuditEventType`, table `ticket_event` to `ticket_audit_event`
5. Rename `FetcherAuditLog` to `FetcherAuditEvent`, `FetcherAuditAction`
   to `FetcherAuditEventType`, table `fetcher_audit_log` to
   `fetcher_audit_event`
6. Rename `FetcherAuditEvent.performed_by_user_id` to `user_id`
   (inherited from `AuditEventMixin`, nullable at DB level)
7. Add a note referencing `BaseAuditLog` and the Audit Trail Conventions
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
| GET | `/api/v1/admin/identity/audit-log` | Admin | `identity/identity-audit-log.md` |
| GET | `/api/v1/admin/settings/audit-log` | Admin | `platform/admin.md` |

### Identity specs (replace app logging with audit trail)

The specs listed in Change 3 "Spec updates required" must be updated to
reference the `IdentityAuditLog` instead of (or in addition to)
INFO-level logging.

---

## Change 7: Fetcher audit trail alignment

**Target files**: `docs/features/platform/fetcher-infrastructure.md`,
`docs/features/platform/fetcher-dashboard.md`, `docs/data-model.md`

**Actions**:

1. Rename the model from `FetcherAuditLog` to `FetcherAuditEvent`
2. Rename the enum from `FetcherAuditAction` to `FetcherAuditEventType`
3. Rename the table from `fetcher_audit_log` to `fetcher_audit_event`
4. Rename the column `performed_by_user_id` to `user_id` — the column is
   now inherited from `AuditEventMixin`. Update all spec references to
   this column in `fetcher-infrastructure.md` and `fetcher-dashboard.md`
5. Change `user_id` constraint from NOT NULL to nullable (inherited from
   `AuditEventMixin`)
6. Rename the API response field `performed_by` to `actor` in
   `fetcher-dashboard.md` to align with the standard response format
   used by all audit trail endpoints
7. Add `from_date`, `to_date`, and `actor` query parameters to the
   `GET /api/v1/fetchers/{fetcher_name}/audit-log` endpoint in
   `fetcher-dashboard.md`, in compliance with the audit trail conventions
   in `docs/features/platform/audit-trail-infrastructure.md`
8. Create a `FetcherAuditLog(BaseAuditLog)` subclass to register this
   audit trail in the global registry at implementation time

**Note**: no database migration is needed for these renames. The fetcher
audit trail has no implementation code yet — these are spec-level changes
only (model names, enum names, table names, column names in
documentation). The actual database objects will be created with the
standard names from the start.

The Fetcher audit trail is already included in the Audit Trail Index
(Change 1).

---

## Change 8: Ticket audit log endpoint alignment

**Target file**: `docs/features/tickets/ticket-audit-log.md` (after rename
from Change 2)

**Actions**:

1. Rename the API endpoint from `GET /api/v1/tickets/{ticket_id}/events`
   to `GET /api/v1/tickets/{ticket_id}/audit-log` to conform to the
   `/audit-log` suffix convention (see `docs/api-spec.md`, Audit Trail
   Endpoint Naming)
2. Add `from_date` and `to_date` query parameters to the endpoint, with
   the same semantics defined in the BaseAuditLog date filtering
   convention
3. Update the `actor` query parameter to accept UUID, username, or
   `system` (aligned with `BaseAuditLog.filter_by_actor()` convention)
4. Update the Endpoint Permission Map in `rbac.md` to reflect the new
   path

**Cross-reference updates required** (files that reference the old
endpoint path `/tickets/{ticket_id}/events`):

A `grep` must be run at application time to find all references to the
old endpoint path. Known locations:

- `docs/features/tickets/ticket-history.md` (being renamed)
- `docs/features/ui/pages/ticket-detail.md`
- `docs/api-spec.md` (if the endpoint is listed)
- `docs/features/identity/rbac.md` (Endpoint Permission Map)

---

## Excluded from scope

The following were considered and explicitly excluded:

| Item | Reason |
|---|---|
| Session events (login/logout) | App-level logging is sufficient; sessions are cleaned up weekly and the audit value is low compared to the volume |
| Product lifecycle changes (SMELT/AIMAAS) | External systems are the source of truth |
| SubmissionRequest / ReleaseRequest state changes | IBS is the source of truth |
| Runtime-configurable retention via SystemSetting | YAGNI — can be added later if needed; the `BaseAuditLog.default_retention_days` attribute provides the extension point |
| `ad_dn` field changes | Field is under evaluation for removal from the data model; no `ad_dn_changed` event type is created. If the field is retained in the future, an event type can be added at that time |

---

## Implementation order (suggested)

0. **Name standardization (prerequisite)** — covers renames from Changes
   5 and 7. Rename all legacy audit trail names across specifications,
   data model, conventions, guardrails, and agent definitions to follow
   the standard `{Domain}AuditEvent` / `{Domain}AuditEventType` /
   `{domain}_audit_event` pattern. This is a spec-only change (no
   implementation code exists yet). Renames:
   - `TicketEvent` → `TicketAuditEvent`
   - `TicketEventType` → `TicketAuditEventType`
   - `ticket_event` (table) → `ticket_audit_event`
   - `FetcherAuditLog` (model) → `FetcherAuditEvent`
   - `FetcherAuditAction` (enum) → `FetcherAuditEventType`
   - `fetcher_audit_log` (table) → `fetcher_audit_event`
   - `create_ticket_event()` (helper) → `TicketAuditLog.log_event()`
   - Endpoint path: `/tickets/{id}/events` → `/tickets/{id}/audit-log`
1. **Audit Trail Infrastructure spec** (Change 1) — create
   `docs/features/platform/audit-trail-infrastructure.md` with
   `BaseAuditLog`, `AuditEventMixin`, naming conventions, atomicity
   rules, actor field semantics, date filtering, and Audit Trail Index
2. **Conventions reference** (Change 1b) — add brief "Audit Trail"
   subsection in `docs/conventions.md` pointing to the infrastructure
   spec
3. **API spec endpoint naming** (Change 1c) — add "Audit Trail Endpoint
   Naming" subsection in `docs/api-spec.md` with the `/audit-log` suffix
   convention
4. **Ticket audit log** (Changes 2 + 8) — rename `ticket-history.md` to
   `ticket-audit-log.md`, refactor content, create `TicketAuditLog`
   subclass, add `from_date`/`to_date`/`actor` to the endpoint
5. **Identity audit log** (Change 3) — create
   `identity-audit-log.md` + update identity specs to replace INFO
   logging with audit events
6. **Setting audit log** (Change 4) — add section to `admin.md`
7. **Fetcher audit trail alignment** (Change 7) — apply remaining
   renames not covered by step 0, create `FetcherAuditLog` subclass,
   add filter params and response field rename
8. **Data model updates** (Change 5) — add `AuditEventMixin`
   documentation, new tables, updated table/column names
9. **Cross-reference and permission updates** (Change 6) — update all
   file references, endpoint paths, and Endpoint Permission Map in
   `rbac.md`

---

## Open questions

1. ~~**Missing event type: `user_updated` for generic field changes**~~ —
   **RESOLVED**: added per-field event types (`email_changed`,
   `full_name_changed`, `manager_changed`) consistent with the existing
   `username_changed` pattern. `ad_dn_changed` is NOT created because
   the field is under evaluation for removal. `user_created` now applies
   to ALL users including AD sync (removed from "Excluded from scope").
   Total IdentityAuditEventType values: 16.

2. ~~**Side effects of deactivation not explicitly audited**~~ —
   **RESOLVED**: each API key revocation produces an individual
   `api_key_revoked` event, created by the centralized
   `api_key_service` (see `docs/features/identity/api-key-service.md`).
   Bulk revocation during deactivation adds
   `{"reason": "user_deactivated"}` to the `detail` JSONB. Session
   invalidation remains excluded from the audit trail.

3. ~~**Actor field naming inconsistency**~~ — **RESOLVED**:
   `performed_by_user_id` is replaced by `user_id`, inherited from
   `AuditEventMixin`. All audit event models share the same nullable
   `user_id` column via the mixin. Subclasses that only record
   human-initiated actions may override `log_event()` to validate that
   `user_id` is provided. `BaseAuditLog` provides a `filter_by_actor()`
   method that operates on the uniform `user_id` column.
   `FetcherRun.triggered_by_user_id` is NOT renamed — it is not an audit
   trail and its descriptive name is appropriate for its domain.

4. **No retrieval endpoint for fetcher audit log** — The
   `fetcher-infrastructure.md` spec does not define a retrieval API for
   fetcher audit events. `fetcher-dashboard.md` defines
   `GET /api/v1/fetchers/{fetcher_name}/audit-log`. Verify that this
   endpoint exists, supports the standard `from_date`/`to_date` parameters,
   and conforms to the `/audit-log` suffix convention. If not, align it as
   part of Change 7.

5. **Indexes not defined for new tables** — `data-model.md` has a global
   "TBD" for indexes. Audit log tables have predictable query patterns
   (filter by `created_at`, `event_type`, `target_user_id`, `setting_key`).
   Should Change 5 (data model updates) define indexes for all audit tables,
   or is this deferred to implementation time?

6. **Guardrail 11 covers only tickets** — Guardrail 11 (AGENTS.md) mandates
   `TicketEvent` creation for every ticket mutation but has no equivalent
   for identity or setting mutations. Should Guardrail 11 be expanded to
   cover all audit trails (e.g., "every identity mutation MUST create an
   `IdentityAuditEvent`"), or should separate guardrails be created?

7. ~~**Event type count: 25 vs 24**~~ — **RESOLVED**: the count is 24
   in both `ticket-history.md` and the Audit Trail Index. No discrepancy
   exists — the original count of 25 was incorrect.
