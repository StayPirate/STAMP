# Draft: Confidential Tickets (Embargo)

## 1. Purpose

Introduce the concept of "Confidential Tickets" in Sentinel to securely handle embargoed vulnerabilities. Confidential tickets restrict read and write access to a specific subset of authorized users, preventing data leaks prior to public disclosure.

## 2. Domain Concepts

- **Confidentiality Flag**: A boolean state (`is_confidential`) on the Ticket entity that determines if the ticket is under embargo.
- **Access Grants**: The mechanism determining who can access a confidential ticket. Access is granted via roles, automated maintainer inheritance (from IBS bugowners), and explicit manual grants.
- **Confidentiality Filtering**: Confidential tickets are excluded at the database query level for unauthorized and unauthenticated users. They do not appear in list results, are not returned by detail endpoints, and leave no visible trace (no placeholders, no redacted entries). Authorized users see confidential tickets normally alongside non-confidential ones.

## 3. Data Model Updates (`docs/data-model.md`)

### 3.1 Ticket Entity Modifications
- Add column `is_confidential`:
  - Type: `BOOLEAN`
  - Default: `FALSE`
  - Nullable: `FALSE`
  - Description: When `TRUE`, access to the ticket and all its related resources is restricted.

### 3.2 New Entity: TicketAccessGrant
A new table to store explicit, manual access grants given by Vulnerability Analysts to specific users.
- `ticket_id`: `UUID`, Primary Key, Foreign Key to `Ticket.id` (ON DELETE RESTRICT)
- `user_id`: `UUID`, Primary Key, Foreign Key to `User.id` (ON DELETE RESTRICT)
- `granted_by_id`: `UUID`, Foreign Key to `User.id` (ON DELETE RESTRICT) (The VA who granted the access)
- `granted_at`: `TIMESTAMPTZ`, Default: `now()`

*Note: ON DELETE RESTRICT is used because tickets and users are never physically deleted in Sentinel (only soft-deleted or deactivated).*

## 4. Authorization Rules (`docs/features/identity/rbac.md`)

When a ticket is `is_confidential=True`, any read/write HTTP request MUST be evaluated against these rules. Access is **GRANTED** if the user meets at least one condition:

1. **Role-based**: The user holds the `Vulnerability Analyst` or `Admin` role.
2. **Explicit Grant**: The user's `id` exists in the `TicketAccessGrant` table for the requested `ticket_id`.
3. **Bugowner (Person)**: The user's `email` matches the `bugowner_email` of any `PackageBugowner` associated with any of the ticket's *currently associated* packages. The email comparison MUST be case-insensitive.
4. **Bugowner (Group)**: The user's `email` matches the `email` of any `PackageBugownerMember` associated with a group bugowner of any *currently associated* package in the ticket. The email comparison MUST be case-insensitive.

*Dynamic Access Note:* Bugowner access is dynamic. A maintainer gains access when a package they support is added to the ticket, and loses access the moment the last package they support is removed from the ticket. "Currently associated packages" means `TicketPackage.deleted_at IS NULL` — soft-deleted (excluded) packages do not grant bugowner access.

If no condition is met, or if the user is unauthenticated, the confidential ticket is **invisible**: it is excluded from list queries and detail endpoints return `404 Not Found`. Unauthenticated users never see confidential tickets.

*Note: System background tasks (Celery fetchers, event consumers) bypass these rules and process confidential tickets normally.*

## 5. API Behavior & Endpoints (`docs/features/tickets/tickets.md`)

### 5.1 Response Schema

Ticket response objects (both list and detail) MUST include the
`is_confidential: boolean` field. This field is always present — there
is no information leakage concern because a user only receives tickets
they are authorized to see (Section 5.4).

### 5.2 Detail Endpoint & Sub-resources
- `GET /api/v1/tickets/{ticket_id}` and all sub-routes (e.g., `/packages`, `/references`, `/cvss`, `/audit-log`) MUST return `404 Not Found` for unauthorized or unauthenticated users accessing a confidential ticket.
- This MUST be enforced centrally via a router-level FastAPI dependency (e.g., `require_accessible_ticket`) that applies the authorization rules from Section 4. The ticket is treated as non-existent for users who do not satisfy any access condition.
- **Evaluation order**: The dependency MUST evaluate conditions in this exact order: (1) ticket existence — if not found, return `404`; (2) confidentiality authorization — if the ticket is confidential and the caller does not satisfy any rule from Section 4, return `404`; (3) soft-delete check — if `deleted_at IS NOT NULL`, return `410`. This order prevents a `410` response from confirming the existence of a confidential ticket to an unauthorized user.

### 5.3 Ticket Creation & Confidentiality Toggle

**Automatic Creation (CVE Ingestion / Track Detection)**: 
Tickets created automatically by the system MUST have `is_confidential=FALSE` by default.

**Manual Creation (`POST /api/v1/tickets`)**: 
Accept an optional `is_confidential` boolean field in the request schema. A ticket can be created as confidential from the start.

**Toggle Endpoint (`PATCH /api/v1/tickets/{ticket_id}/confidentiality`)**:
Toggle the confidentiality status of a ticket.
- **Access level**: Vulnerability Analyst
- **Request body**: `{ "is_confidential": boolean }`
- **Response body**: The updated ticket object in the standard `{"data": <ticket>}` envelope.
- **Idempotency**: If the ticket already has the requested status, the operation returns 200 OK without creating an audit event or modifying the database.
- **Concurrency**: Acquires `FOR UPDATE` on the `Ticket` row (because it modifies the Ticket entity, fulfilling the Concurrency Control convention in `tickets.md`). It does NOT go through `ticket_mutations` as it is not gate-relevant data.
- **Audit**: Creates `TicketAuditEvent` with `event_type = confidentiality_changed`.

| Status | Code | Condition |
|--------|------|-----------|
| 200    | -    | Success (or already in requested state) |
| 404    | `TICKET_NOT_FOUND` | Ticket not found |
| 409    | `TICKET_INVALID_TRANSITION` | Ticket is in Duplicated status (revert first) |

### 5.4 Access Grant Management
New endpoints to manage `TicketAccessGrant` records. Available ONLY to users with the `Vulnerability Analyst` role. 

**List Access Grants (`GET /api/v1/tickets/{ticket_id}/access`)**:
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
  *Note: Unpaginated because explicit access grants per ticket are a bounded dataset (typically a handful of users).*

| Status | Code | Condition |
|--------|------|-----------|
| 200    | -    | Success |
| 404    | `TICKET_NOT_FOUND` | Ticket not found (or confidential and caller is not authorized) |
| 409    | `TICKET_NOT_CONFIDENTIAL` | Ticket is not confidential |

**Grant Access (`POST /api/v1/tickets/{ticket_id}/access`)**:
Grant explicit access to a user on a confidential ticket.
- **Access level**: Vulnerability Analyst
- **Request body**: `{ "user": str }` (Accepts UUID or username per User Identifier Resolution convention; backend resolves via `resolve_user_identifier`).
- **Idempotency**: If the grant already exists, returns 200 OK with the existing grant data, without creating an audit event. Otherwise, creates the grant and returns 201 Created.
- **Response** (200 OK or 201 Created): Same object format as the list response.
- **Audit**: Creates `TicketAuditEvent` with `event_type = access_grant_added`.

| Status | Code | Condition |
|--------|------|-----------|
| 201    | -    | Grant created |
| 200    | -    | Grant already exists (idempotent success) |
| 404    | `TICKET_NOT_FOUND` | Ticket not found (or confidential and caller is not authorized) |
| 404    | `USER_NOT_FOUND` | Target user not found |
| 409    | `TICKET_NOT_CONFIDENTIAL` | Ticket is not confidential |

**Revoke Access (`DELETE /api/v1/tickets/{ticket_id}/access/{user}`)**:
Revoke explicit access from a user on a confidential ticket. The `{user}`
path parameter is of type `str` and accepts either a UUID or username.
- **Access level**: Vulnerability Analyst
- **Idempotency**: If the grant does not exist, returns 204 No Content without creating an audit event.
- **Response**: 204 No Content.
- **Audit**: Creates `TicketAuditEvent` with `event_type = access_grant_removed`.

| Status | Code | Condition |
|--------|------|-----------|
| 204    | -    | Grant revoked (or did not exist — idempotent success) |
| 404    | `TICKET_NOT_FOUND` | Ticket not found (or confidential and caller is not authorized) |
| 404    | `USER_NOT_FOUND` | Target user not found |
| 409    | `TICKET_NOT_CONFIDENTIAL` | Ticket is not confidential |

### 5.5 Confidentiality Filtering

Confidential tickets are filtered at the database query level.
Unauthorized and unauthenticated users never see them — no
placeholders, no redacted entries, no trace of their existence.

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

When a ticket's `is_confidential` flag is set to `FALSE` (embargo lifted) or the ticket is soft-deleted, the explicit `TicketAccessGrant` records remain in the database inertly.

To prevent infinite database growth, a Celery Beat background task (`cleanup_stale_ticket_access_grants`) will be implemented:
- **Type**: Plain Celery Beat task (NOT a `BaseFetcher`, as it does not fetch external data).
- **Schedule**: Weekly, Sunday at 04:00 UTC.
- **Logic**: Deletes all `TicketAccessGrant` records belonging to tickets where `is_confidential = FALSE` AND `updated_at` is older than 14 days.

This single condition covers all cases:
- Embargo lifted (ticket made non-confidential) → grants cleaned after 14 days
- Soft-deleted non-confidential ticket → `is_confidential` is already `FALSE` → grants cleaned after 14 days
- Soft-deleted confidential ticket → `is_confidential` is still `TRUE` → grants preserved. If the ticket is later restored, all grants are intact. To clean them, an Admin must first restore the ticket, then a VA removes confidentiality — the cleanup runs 14 days later

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

- **Ticket Detail**: Display a prominent "Confidential / Embargoed" badge or banner when `is_confidential=True`.
- **Access Grants Manager**: A new section in the Ticket Detail sidebar (visible only to VAs) to search for users and add/remove them from the manual access grants list.
- **Ticket List**: Confidential tickets only appear for authorized users. No special rendering is needed — they display normally alongside non-confidential tickets. A small "Confidential" badge may be shown to indicate the ticket's status to authorized viewers.

## 10. Cross-references

- `docs/api-spec.md` — global API conventions, error code registry, envelope format
- `docs/features/tickets/tickets.md` — ticket lifecycle, concurrency control, `FOR UPDATE` requirement
- `docs/features/tickets/ticket-audit-log.md` — audit event contract, detail JSONB schema
- `docs/features/identity/rbac.md` — Endpoint Permission Map
- `docs/features/packages/package-bugowner.md` — bugowner resolution for dynamic access

## 11. Open Points & Edge Cases

### 11.1 IBS to Sentinel User Matching (Identity Resolution) — RESOLVED

**Decision**: Match via **email**, not username.

Empirical verification showed that IBS userids frequently differ from AD
`sAMAccountName` values (e.g., an IBS userid may not match the user's AD
`sAMAccountName`, or may have no AD counterpart at all). Username-based
matching is unreliable.

The `PackageBugowner` and `PackageBugownerMember` tables already store
email addresses (`bugowner_email` and `email` respectively), populated
from IBS `/person/{userid}` API calls. The authorization check joins on:

- Person bugowner: `User.email == PackageBugowner.bugowner_email`
- Group member: `User.email == PackageBugownerMember.email`

No data model changes are required. IBS users without an AD/Sentinel
counterpart (no matching email) will not receive automatic access; VAs
can grant explicit access via `TicketAccessGrant` as a workaround.

### 11.2 Duplicate Ticket Management — RESOLVED

**Decision**: No special handling. `duplicate_of_id` is returned as-is.

Cross-confidentiality duplicates (a non-confidential ticket marked as
duplicate of a confidential one, or vice versa) are a rare edge case.
The existing `require_accessible_ticket` dependency already protects the
confidential ticket: if an unauthorized user follows the
`duplicate_of_id` link, they receive a `404 Not Found` —
indistinguishable from a non-existent ticket.

Adding filtering logic for `duplicate_of_id` (checking confidentiality
of the target ticket and the caller's access on every serialization)
would introduce query overhead and code complexity disproportionate to
the rarity of the scenario.

### 11.3 Notification Security — RESOLVED

**Decision**: Architectural constraint established for future spec.

The notification system is not yet specified or implemented. The
following constraint MUST be respected by any future notification
specification:

> The notification system MUST send notifications related to
> confidential tickets ONLY to users who satisfy the authorization
> rules defined in Section 4. The notification content (including email
> subject, body, and metadata) MUST be treated as confidential data.

---

## 12. Review Findings (all resolved)

The following findings were raised by automated reviewers
(`@security-reviewer`, `@spec-gap-analyzer`, `@api-convention-reviewer`,
`@spec-coherence-reviewer`) and have been resolved.

### Critical

#### 12.1 Admin excluded but has exclusive ticket operations — RESOLVED

**Decision**: Admin has full access to confidential tickets.

Admin is included in the authorized roles (Section 4, rule 1) alongside
Vulnerability Analyst. This ensures Admin-exclusive operations
(soft-delete, restore, CVE removal) work on confidential tickets without
special bypass logic.

#### 12.2 Public endpoints with confidentiality logic — RESOLVED

**Decision**: DB-level filtering for all unauthorized and unauthenticated
users. No redaction, no placeholders.

Confidential tickets are excluded at the database query level.
Unauthenticated users see only non-confidential tickets. Authenticated
users see non-confidential tickets plus confidential tickets they are
authorized to access. Detail endpoints return `404 Not Found` for
unauthorized access to confidential tickets (indistinguishable from a
non-existent ticket). The `TICKET_ACCESS_DENIED` error code has been
removed — it is no longer needed.

#### 12.3 Restore of soft-deleted ticket after access grant cleanup — RESOLVED

**Decision**: Simplified cleanup condition — delete grants only where
`is_confidential = FALSE` AND `updated_at` older than 14 days.

This single condition avoids the problem entirely: confidential tickets
(whether soft-deleted or not) keep `is_confidential = TRUE`, so the
cleanup never touches their grants. If a soft-deleted confidential
ticket is restored, all grants are intact. The `updated_at` field is
updated automatically by the ORM whenever the Ticket row is modified
(including the confidentiality toggle), so the 14-day window resets
correctly.

### High

#### 12.4 Maintainer dashboard leaks confidential ticket data — RESOLVED

**Decision**: Apply confidentiality filtering to the maintainer dashboard
as defense in depth.

The dashboard query already matches on bugowner email, which coincides
with authorization rules 3/4 (Section 4). However, the confidentiality
filter is applied explicitly in Section 5.4 to protect against future
changes to the dashboard query logic.

#### 12.5 Response format: User Reference Object convention — RESOLVED

**Decision**: Response schema updated to use standard User Reference
Objects. The grantee is nested in a `user` object (`{id, username,
full_name}`) and `granted_by` is a full User Reference Object instead
of a bare username string.

#### 12.6 Request field should accept UUID or username — RESOLVED

**Decision**: Request field renamed from `username` to `user` (type
`str`). Accepts both UUID and username per the User Identifier
Resolution convention. Backend resolves via `resolve_user_identifier`.

#### 12.7 Audit event detail vs old_value/new_value — RESOLVED

**Decision**: All three event types updated to use `old_value`/`new_value`
instead of `detail`. `confidentiality_changed` uses `"true"`/`"false"`
strings. `access_grant_added` uses `NULL`/username.
`access_grant_removed` uses username/`NULL`. All set `detail` to `NULL`
(no structured context needed).

#### 12.8 DELETE path parameter should support dual-lookup — RESOLVED

**Decision**: Path parameter renamed from `{user_id}` to `{user}` (type
`str`). Accepts both UUID and username per the User Identifier
Resolution convention, consistent with other endpoints in the project.

### Medium

#### 12.9 Sort-order leakage in unfiltered list — RESOLVED

**Decision**: Resolved automatically by the DB filtering design (12.2).

Confidential tickets are excluded from query results for unauthorized
users — there are no placeholders whose sort position could leak
metadata.

#### 12.10 CVE detail reveals confidential ticket association — RESOLVED

**Decision**: Resolved by the DB filtering design (12.2). Section 5.4
specifies that the ticket reference is omitted entirely from the CVE
detail response for unauthorized and unauthenticated users.

#### 12.11 Email comparison case-sensitivity — RESOLVED

**Decision**: Email comparison in authorization rules 3 and 4 (Section 4)
MUST be case-insensitive. Specified explicitly in the authorization
rules rather than relying on storage normalization, since emails
originate from external sources (AD, IBS) outside Sentinel's control.

#### 12.12 Cleanup timestamp ambiguity — RESOLVED

**Decision**: Resolved by the simplified cleanup condition (12.3).
Using `updated_at` is correct and desirable: if a non-confidential
ticket is still actively modified, the 14-day window resets — but the
grants are inert anyway (ticket is non-confidential) and the DB cost
is negligible.

#### 12.13 Soft-deleted packages and bugowner access — RESOLVED

**Decision**: "Currently associated packages" explicitly defined as
`TicketPackage.deleted_at IS NULL` in Section 4. A maintainer loses
bugowner-based access when their package is soft-deleted from the
ticket.

#### 12.14 Terminology: "public" vs "non-confidential" — RESOLVED

**Decision**: All instances of "public" referring to non-confidential
tickets have been replaced with "non-confidential" to avoid confusion
with the RBAC "Public" access level.

#### 12.15 Confidentiality toggle on Duplicated tickets — RESOLVED

**Decision**: Toggle is blocked (409) for tickets in Duplicated status,
consistent with the rule in `tickets.md` that blocks all modifications
to Duplicated tickets. The VA must revert the duplicate first, toggle
confidentiality, then re-mark as duplicate if needed.

## 13. Review Findings — Second Pass (all resolved)

Findings from the second review pass (`@security-reviewer`,
`@spec-gap-analyzer`, `@api-convention-reviewer`,
`@spec-coherence-reviewer`). No critical findings.

### High

#### 13.1 Admin cannot toggle or manage access grants — NOT A FINDING

**Decision**: Not a real finding. Admin can self-assign the VA role
via the role management endpoints, resolving any "orphan ticket"
scenario without spec changes.

#### 13.2 `is_confidential` not in ticket response schemas — RESOLVED

**Decision**: Added Section 5.1 (Response Schema) specifying that
`is_confidential: boolean` is always present in ticket response
objects (list and detail).

#### 13.3 Toggle endpoint missing response body — RESOLVED

**Decision**: Toggle endpoint returns the updated ticket object in
the standard `{"data": <ticket>}` envelope, consistent with other
PATCH endpoints on tickets.

#### 13.4 `TICKET_DUPLICATED` error code not registered — RESOLVED

**Decision**: Replaced with existing `TICKET_INVALID_TRANSITION`
(409), consistent with the convention in `tickets.md` for all
status-based operation blocks on Duplicated tickets.

### Medium

#### 13.5 Path parameter `{id}` vs `{ticket_id}` — RESOLVED

**Decision**: Renamed all path parameters from `{id}` to
`{ticket_id}` to match existing ticket endpoint conventions.

#### 13.6 Evaluation order in `require_accessible_ticket` — RESOLVED

**Decision**: Evaluation order specified in Section 5.2:
(1) ticket existence → 404, (2) confidentiality authorization → 404,
(3) soft-delete check → 410.

#### 13.7 DELETE missing `USER_NOT_FOUND` error — RESOLVED

**Decision**: Added `404 USER_NOT_FOUND` to the DELETE endpoint
error table. A non-existent user returns 404; an existing user
without a grant returns 204 (idempotent no-op).

#### 13.8 `granted_by_id` FK action not specified — RESOLVED

**Decision**: Added explicit `ON DELETE RESTRICT` for `granted_by_id`
in Section 3.2, consistent with the other FK columns.

#### 13.9 Audit trail cross-ticket leakage via duplicate — NOT A FINDING

**Decision**: Edge case too rare to warrant handling or
documentation. The ticket ID is an opaque identifier and navigation
returns 404 regardless.

#### 13.10 `duplicate_of_id` leakage — NOT A FINDING

**Decision**: Already covered by 11.2. No additional documentation
needed.

### Low

#### 13.11 "ACL" terminology — RESOLVED

**Decision**: All instances of "ACL" replaced with "access grants"
throughout the spec for vocabulary consistency.

#### 13.12 Event type casing — RESOLVED

**Decision**: All event type references in prose normalized to
lowercase (`confidentiality_changed`, `access_grant_added`,
`access_grant_removed`), matching the stored enum convention.

#### 13.13 Cleanup schedule — RESOLVED

**Decision**: Schedule specified as "Weekly, Sunday at 04:00 UTC".

#### 13.14 Deactivated users in access grants list — RESOLVED

**Decision**: Added `active: boolean` to the User Reference Object
in the access grants list response. Deactivated users appear in the
list with `active: false`, allowing VAs to identify inert grants.

#### 13.15 Timing-safe 404 — NOT A FINDING

**Decision**: Not relevant for Sentinel's threat model (internal
application with authenticated users via SSO). No spec changes.