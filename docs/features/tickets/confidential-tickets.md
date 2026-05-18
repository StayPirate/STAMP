# Confidential Tickets (Embargo)

## 1. Purpose

Introduce the concept of "Confidential Tickets" in Sentinel to securely
handle embargoed vulnerabilities. Confidential tickets restrict read and
write access to a specific subset of authorized users, preventing data
leaks prior to public disclosure.

## 2. Domain Concepts

- **Confidentiality Flag**: A boolean state (`is_confidential`) on the
  Ticket entity that determines if the ticket is under embargo.
- **Access Grants**: The mechanism determining who can access a
  confidential ticket. Access is granted via roles, automated maintainer
  inheritance (from IBS bugowners), and explicit manual grants.
- **Confidentiality Filtering**: Confidential tickets are excluded at
  the database query level for unauthorized and unauthenticated users.
  They do not appear in list results, are not returned by detail
  endpoints, and leave no visible trace (no placeholders, no redacted
  entries). Authorized users see confidential tickets normally alongside
  non-confidential ones.

## 3. Data Model Updates (`docs/data-model.md`)

### 3.1 Ticket Entity Modifications

- Add column `is_confidential`:
  - Type: `BOOLEAN`
  - Default: `FALSE`
  - Nullable: `FALSE`
  - Description: When `TRUE`, access to the ticket and all its related
    resources is restricted.

### 3.2 New Entity: TicketAccessGrant

A new table to store explicit, manual access grants given by
Vulnerability Analysts to specific users.

- `ticket_id`: `UUID`, Primary Key, Foreign Key to `Ticket.id`
  (ON DELETE RESTRICT)
- `user_id`: `UUID`, Primary Key, Foreign Key to `User.id`
  (ON DELETE RESTRICT)
- `granted_by_id`: `UUID`, Foreign Key to `User.id`
  (ON DELETE RESTRICT) (The VA who granted the access)
- `granted_at`: `TIMESTAMPTZ`, Default: `now()`

*Note: ON DELETE RESTRICT is used because tickets and users are never
physically deleted in Sentinel (only soft-deleted or deactivated).*

## 4. Authorization Rules (`docs/features/identity/rbac.md`)

When a ticket is `is_confidential=True`, any read/write HTTP request
MUST be evaluated against these rules. Access is **GRANTED** if the user
meets at least one condition:

1. **Role-based**: The user holds the `Vulnerability Analyst` or `Admin`
   role.
2. **Explicit Grant**: The user's `id` exists in the `TicketAccessGrant`
   table for the requested `ticket_id`.
3. **Bugowner (Person)**: The user's `email` matches the
   `bugowner_email` of any `PackageBugowner` associated with any of the
   ticket's *currently associated* packages. The email comparison MUST
   be case-insensitive.
4. **Bugowner (Group)**: The user's `email` matches the `email` of any
   `PackageBugownerMember` associated with a group bugowner of any
   *currently associated* package in the ticket. The email comparison
   MUST be case-insensitive.

*Dynamic Access Note:* Bugowner access is dynamic. A maintainer gains
access when a package they support is added to the ticket, and loses
access the moment the last package they support is removed from the
ticket. "Currently associated packages" means
`TicketPackage.deleted_at IS NULL` — soft-deleted (excluded) packages do
not grant bugowner access.

If no condition is met, or if the user is unauthenticated, the
confidential ticket is **invisible**: it is excluded from list queries
and detail endpoints return `404 Not Found`. Unauthenticated users never
see confidential tickets.

*Note: System background tasks (Celery fetchers, event consumers) bypass
these rules and process confidential tickets normally.*

## 5. API Behavior & Endpoints (`docs/features/tickets/tickets.md`)

### 5.1 Response Schema

Ticket response objects (both list and detail) MUST include the
`is_confidential: boolean` field. This field is always present — there
is no information leakage concern because a user only receives tickets
they are authorized to see (Section 5.6).

### 5.2 Detail Endpoint & Sub-resources

- `GET /api/v1/tickets/{ticket_id}` and all sub-routes (e.g.,
  `/packages`, `/references`, `/cvss`, `/audit-log`) MUST return
  `404 Not Found` for unauthorized or unauthenticated users accessing a
  confidential ticket.
- This MUST be enforced centrally via a router-level FastAPI dependency
  (e.g., `require_accessible_ticket`) that applies the authorization
  rules from Section 4. The ticket is treated as non-existent for users
  who do not satisfy any access condition.
- **Evaluation order**: The dependency MUST evaluate conditions in this
  exact order: (1) ticket existence — if not found, return `404`;
  (2) confidentiality authorization — if the ticket is confidential and
  the caller does not satisfy any rule from Section 4, return `404`;
  (3) soft-delete check — if `deleted_at IS NOT NULL`, return `410`.
  This order prevents a `410` response from confirming the existence of
  a confidential ticket to an unauthorized user.

### 5.3 Ticket Creation

**Automatic Creation (CVE Ingestion / Track Detection)**:
Tickets created automatically by the system MUST have
`is_confidential=FALSE` by default.

**Manual Creation (`POST /api/v1/tickets`)**:
Accept an optional `is_confidential` boolean field in the request
schema. A ticket can be created as confidential from the start.

### 5.4 Set Confidentiality

```
POST /api/v1/tickets/{ticket_id}/set-confidentiality
```

Sets the confidentiality status of a ticket.

- **Access level**: Vulnerability Analyst
- **Request body**: `{ "is_confidential": boolean }`
- **Response body**: The updated ticket object in the standard
  `{"data": <ticket>}` envelope.
- **Idempotency**: If the ticket already has the requested status, the
  operation returns 200 OK without creating an audit event or modifying
  the database.
- **Concurrency**: Acquires `FOR UPDATE` on the `Ticket` row (because
  it modifies the Ticket entity, fulfilling the Concurrency Control
  convention in `tickets.md`). It does NOT go through
  `ticket_mutations` as it is not gate-relevant data.
- **Audit**: Creates `TicketAuditEvent` with
  `event_type = confidentiality_changed`.

| Status | Code | Condition |
|--------|------|-----------|
| 200    | -    | Success (or already in requested state) |
| 404    | `TICKET_NOT_FOUND` | Ticket not found |
| 409    | `TICKET_INVALID_TRANSITION` | Ticket is in Duplicated status (revert first) |

### 5.5 Access Grant Management

New endpoints to manage `TicketAccessGrant` records. Available ONLY to
users with the `Vulnerability Analyst` role.

#### List Access Grants

```
GET /api/v1/tickets/{ticket_id}/access
```

List all users with explicit access grants for a confidential ticket.

- **Access level**: Vulnerability Analyst
- **Response** (200 OK, unpaginated):
  ```json
  {
    "data": [
      {
        "user": {
          "id": "uuid",
          "username": "jdoe",
          "full_name": "John Doe",
          "active": true
        },
        "granted_at": "2025-03-15T10:30:00Z",
        "granted_by": {
          "id": "uuid",
          "username": "asmith",
          "full_name": "Alice Smith",
          "active": true
        }
      }
    ]
  }
  ```
  *Note: Unpaginated because explicit access grants per ticket are a
  bounded dataset (typically a handful of users).*

| Status | Code | Condition |
|--------|------|-----------|
| 200    | -    | Success |
| 404    | `TICKET_NOT_FOUND` | Ticket not found (or confidential and caller is not authorized) |
| 409    | `TICKET_NOT_CONFIDENTIAL` | Ticket is not confidential |

#### Grant Access

```
POST /api/v1/tickets/{ticket_id}/access
```

Grant explicit access to a user on a confidential ticket.

- **Access level**: Vulnerability Analyst
- **Request body**: `{ "user": str }` (Accepts UUID or username per User
  Identifier Resolution convention; backend resolves via
  `resolve_user_identifier`).
- **Idempotency**: If the grant already exists, returns 200 OK with the
  existing grant data (reflecting the original `granted_by` and
  `granted_at`), without creating an audit event. Otherwise, creates the
  grant and returns 201 Created.
- **Response** (200 OK or 201 Created): The grant object wrapped in the
  standard `{"data": <grant>}` envelope. The grant object has the same
  shape as items in the list response.
- **Audit**: Creates `TicketAuditEvent` with
  `event_type = access_grant_added`.

| Status | Code | Condition |
|--------|------|-----------|
| 201    | -    | Grant created |
| 200    | -    | Grant already exists (idempotent success) |
| 404    | `TICKET_NOT_FOUND` | Ticket not found (or confidential and caller is not authorized) |
| 404    | `USER_NOT_FOUND` | Target user not found |
| 409    | `TICKET_NOT_CONFIDENTIAL` | Ticket is not confidential |

#### Revoke Access

```
DELETE /api/v1/tickets/{ticket_id}/access/{user}
```

Revoke explicit access from a user on a confidential ticket. The
`{user}` path parameter is of type `str` and accepts either a UUID or
username.

- **Access level**: Vulnerability Analyst
- **Idempotency**: If the grant does not exist, returns 204 No Content
  without creating an audit event.
- **Response**: 204 No Content.
- **Audit**: Creates `TicketAuditEvent` with
  `event_type = access_grant_removed`.

| Status | Code | Condition |
|--------|------|-----------|
| 204    | -    | Grant revoked (or did not exist — idempotent success) |
| 404    | `TICKET_NOT_FOUND` | Ticket not found (or confidential and caller is not authorized) |
| 404    | `USER_NOT_FOUND` | Target user not found |
| 409    | `TICKET_NOT_CONFIDENTIAL` | Ticket is not confidential |

### 5.6 Confidentiality Filtering

Confidential tickets are filtered at the database query level.
Unauthorized and unauthenticated users never see them — no placeholders,
no redacted entries, no trace of their existence.

**Ticket List (`GET /api/v1/tickets`)**:
The list query includes only non-confidential tickets plus confidential
tickets for which the current user satisfies at least one authorization
rule from Section 4. For unauthenticated users, only non-confidential
tickets are returned. Pagination counts reflect only the tickets visible
to the caller.

**Maintainer Dashboard (`GET /api/v1/my/packages/*`)**:
The maintainer dashboard endpoints MUST apply the same confidentiality
filtering as the ticket list. Although the bugowner email match used by
the dashboard already coincides with authorization rules 3 and 4
(Section 4), the confidentiality filter MUST be applied explicitly as
defense in depth — protecting against future changes to the dashboard
query logic that might inadvertently bypass the authorization check.

**CVE Details (`GET /api/v1/cves/{id}`)**:
If the CVE is linked to a confidential ticket that the caller is not
authorized to access (or is unauthenticated), the ticket reference MUST
be omitted entirely from the response. The caller sees no indication
that a ticket exists for this CVE.

## 6. Audit Trail (`docs/features/tickets/ticket-audit-log.md`)

Add three new `TicketAuditEventType` values:

| `event_type` | Trigger | `user_id` | `old_value` | `new_value` | `comment` | `detail` |
|---|---|---|---|---|---|---|
| `confidentiality_changed` | `is_confidential` toggled | VA user | `"true"` or `"false"` | `"true"` or `"false"` | `NULL` | `NULL` |
| `access_grant_added` | User manually added to access grants | VA user | `NULL` | Target username | `NULL` | `NULL` |
| `access_grant_removed` | User manually removed from access grants | VA user | Target username | `NULL` | `NULL` | `NULL` |

## 7. Lifecycle & Stale Access Grant Cleanup

When a ticket's `is_confidential` flag is set to `FALSE` (embargo
lifted) or the ticket is soft-deleted, the explicit `TicketAccessGrant`
records remain in the database inertly.

To prevent infinite database growth, a Celery Beat background task
(`cleanup_stale_ticket_access_grants`) will be implemented:

- **Type**: Plain Celery Beat task (NOT a `BaseFetcher`, as it does not
  fetch external data).
- **Schedule**: Weekly, Sunday at 04:00 UTC.
- **Logic**: Deletes all `TicketAccessGrant` records belonging to
  tickets where `is_confidential = FALSE` AND `updated_at` is older
  than 14 days.

This single condition covers all cases:

- Embargo lifted (ticket made non-confidential) → grants cleaned after
  14 days
- Soft-deleted non-confidential ticket → `is_confidential` is already
  `FALSE` → grants cleaned after 14 days
- Soft-deleted confidential ticket → `is_confidential` is still `TRUE`
  → grants preserved. If the ticket is later restored, all grants are
  intact. To clean them, an Admin must first restore the ticket, then a
  VA removes confidentiality — the cleanup runs 14 days later

## 8. Error Codes Registry (`docs/api-spec.md`)

The following new error codes must be added to the registry:

| Code | Prefix Domain | Meaning |
|------|---------------|---------|
| `TICKET_NOT_CONFIDENTIAL` | `TICKET_*` | Operation requires a confidential ticket but the ticket is non-confidential |

*(Note: `TICKET_ACCESS_DENIED` is not needed — unauthorized access to
confidential tickets returns `404 TICKET_NOT_FOUND`, indistinguishable
from a non-existent ticket. `TICKET_ACCESS_GRANT_ALREADY_EXISTS` and
`TICKET_ACCESS_GRANT_NOT_FOUND` are not needed due to the idempotent
design of the access grant endpoints.)*

## 9. UI Requirements (`docs/features/ui/pages.md`)

- **Ticket Detail**: Display a prominent "Confidential / Embargoed"
  badge or banner when `is_confidential=True`.
- **Access Grants Manager**: A new section in the Ticket Detail sidebar
  (visible only to VAs) to search for users and add/remove them from the
  manual access grants list.
- **Ticket List**: Confidential tickets only appear for authorized
  users. No special rendering is needed — they display normally alongside
  non-confidential tickets. A small "Confidential" badge may be shown to
  indicate the ticket's status to authorized viewers.

## 10. Cross-references

- `docs/api-spec.md` — global API conventions, error code registry,
  envelope format
- `docs/features/tickets/tickets.md` — ticket lifecycle, concurrency
  control, `FOR UPDATE` requirement
- `docs/features/tickets/ticket-audit-log.md` — audit event contract,
  detail JSONB schema
- `docs/features/identity/rbac.md` — Endpoint Permission Map
- `docs/features/packages/package-bugowner.md` — bugowner resolution for
  dynamic access
