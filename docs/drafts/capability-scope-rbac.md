# Draft: RBAC Evolution — Capability + Scope (Static)

## Status

**Draft** — not yet approved for implementation.

## Motivation

The current RBAC model uses monolithic roles: each role implies a fixed set
of operations determined by the Permission Matrix. This works well for few
roles but becomes problematic when new access profiles are needed that share
most capabilities with an existing role but differ in resource visibility.

The immediate driver is the need for an `automation_agent` role — bot accounts
that perform the same ticket operations as Vulnerability Analysts but MUST NOT
access confidential tickets. Adding this as a simple enum value forces every
endpoint decorator to enumerate all roles that can use it, creating maintenance
burden and risk of omission as roles grow.

## Design Decision

Evolve the RBAC model from monolithic roles to roles composed of two
orthogonal dimensions:

- **Capability** — what operations a user can perform (static enum)
- **Scope** — what resources a user can see/operate on (static enum)

Both dimensions remain static (defined in code as enums). Adding a new access
profile requires a code change (PR + deploy) — there is no runtime role
management by admins. This preserves simplicity while eliminating the
per-endpoint role enumeration problem.

## Model

### Capability Enum

Capabilities are grouped by functional domain. Each capability covers a
cohesive set of operations that would logically be granted or denied together.

#### Vulnerability Analyst capabilities

| Capability | Operations covered |
|---|---|
| `create_ticket` | Create ticket manually |
| `triage_ticket` | Assign/reassign ticket, change ticket status (all transitions: ignore, reopen, duplicate, revert-duplicate), associate CVE with ticket, set/update severity override |
| `manage_packages` | Add/remove packages from tickets, exclude/restore (package, track, product), change track affectedness status, override product eligibility |
| `manage_cvss` | Add/edit/delete SUSE CVSS assessments |
| `manage_references` | Add/edit/delete ticket references |
| `manage_confidentiality` | Set ticket confidentiality flag, list/grant/revoke access grants |

#### Admin capabilities

| Capability | Operations covered |
|---|---|
| `manage_users` | Update user fields, manage user roles, reset password, deactivate/reactivate, unlock, view deactivation impact, view/revoke all API keys, view admin-scoped identity audit log (self-scoped audit log is Authenticated, not capability-protected) |
| `manage_role_mappings` | AD role mapping CRUD, preview role mapping |
| `manage_settings` | View/update system settings, view settings audit log |
| `manage_fetchers` | Trigger manual fetcher run, enable/disable fetchers, modify fetcher config, view fetcher audit log, view error tracebacks |
| `admin_ticket_ops` | Remove CVE from ticket, soft-delete/restore tickets, view deleted tickets |

### Scope Enum

| Scope | Meaning |
|---|---|
| `all` | Unrestricted — all tickets visible including confidential |
| `non_confidential` | Confidential tickets are invisible by default (see Access Grant Override below) |

Multi-role scope resolution: if any of the user's roles has scope `all`,
the effective scope is `all`. Otherwise, the effective scope is
`non_confidential`. If a third scope value is introduced in the future,
the resolution rule will be revisited with full knowledge of the actual
requirement.

### Predefined Roles (static map in code)

| Role | Capabilities | Scope |
|---|---|---|
| `admin` | `manage_users`, `manage_role_mappings`, `manage_settings`, `manage_fetchers`, `admin_ticket_ops` | `all` |
| `vulnerability_analyst` | `create_ticket`, `triage_ticket`, `manage_packages`, `manage_cvss`, `manage_references`, `manage_confidentiality` | `all` |
| `automation_agent` | `create_ticket`, `triage_ticket`, `manage_packages`, `manage_cvss`, `manage_references` | `non_confidential` |

Design notes:

- `admin` does NOT inherit VA capabilities. A user needing both must hold
  both roles (unchanged from current design).
- `automation_agent` does NOT have `manage_confidentiality` — bots cannot
  set the confidentiality flag or manage access grants.
- A user holding multiple roles receives the union of all capabilities and
  the least restrictive scope (i.e., if any role has `all`, the effective
  scope is `all`). This is intentional: if an admin assigns both
  `vulnerability_analyst` and `automation_agent` to the same user, the
  effective scope is `all`. The admin is responsible for role assignments
  — the system does not enforce mutual exclusivity between roles. If the
  combination is unintended, the admin removes the extra role.
- A user with no roles has an effective scope of `non_confidential`. They
  can still access specific confidential tickets via `TicketAccessGrant`
  or bugowner matching — these per-ticket mechanisms are independent of
  scope.

### Access Levels (unchanged, not roles)

These are not roles and do not carry capabilities or scope:

| Level | Meaning |
|---|---|
| **Public** | No authentication required. Read-only access to public data. |
| **Authenticated** | Valid session required, any role or no role. Own profile, own API keys, maintainer dashboard. |

Endpoints at these levels do not check capabilities — they check only
authentication state.

## Scope and Confidential Ticket Visibility

The scope determines the **default** visibility of confidential tickets.
It does NOT override explicit per-ticket access mechanisms.

A ticket is visible to a user if ANY of the following is true:

1. The ticket is not confidential (always visible to everyone)
2. The user's effective scope is `all`
3. The user has an explicit `TicketAccessGrant` for this ticket
4. The user's email matches a `PackageBugowner` (person) for a package
   associated with this ticket
5. The user's email matches a `PackageBugownerMember` (group member) for
   a package associated with this ticket

This means a VA can grant explicit access to a confidential ticket to a bot
with `non_confidential` scope. The grant overrides the scope restriction for
that specific ticket only. The grant provides full access (read and write)
— the bot can perform any operation its capabilities allow on the granted
ticket. There is no read-only vs read-write distinction in access grants;
this is consistent with how grants work for all users.

Note that visibility alone does not imply write access. An authenticated
user with no roles who receives a `TicketAccessGrant` can see the ticket
but cannot modify it — they have no capabilities. An `automation_agent`
with a grant can both see and modify the ticket because they have
capabilities like `triage_ticket` and `manage_packages`. The two checks
are independent:

- **Scope** (+ grant/bugowner) determines: *can you see this ticket?*
- **Capability** determines: *can you perform this operation?*

Both checks must pass for a write operation to succeed.

The `confidential_ticket_filter()` function signature changes only the
`caller_is_privileged` parameter. Other parameters (`caller_user_id`,
`caller_email`) remain unchanged — they are still needed for grant and
bugowner lookups:

```python
# Before
def confidential_ticket_filter(
    ...,
    caller_is_privileged: bool,     # True if VA or Admin
    caller_user_id: UUID | None,
    caller_email: str | None,
    ...
)

# After
def confidential_ticket_filter(
    ...,
    caller_scope: Scope | None,     # None for unauthenticated
    caller_user_id: UUID | None,
    caller_email: str | None,
    ...
)
```

When `caller_scope` is `None` (unauthenticated), the function
short-circuits: only non-confidential tickets are returned, and
grant/bugowner checks are skipped (no user identity to match against).

## Endpoint Authorization Pattern

### Current (pure RBAC)

```python
@router.post("/tickets")
async def create_ticket(
    ...,
    _: User = Depends(require_role(Role.VULNERABILITY_ANALYST)),
):
```

### New (capability-based)

```python
@router.post("/tickets")
async def create_ticket(
    ...,
    _: User = Depends(require_capability(Capability.CREATE_TICKET)),
):
```

The `require_capability()` dependency:

1. Extracts the current user from the session/token
2. Loads the user's roles
3. Checks if any of the user's roles includes the required capability
   (using the static role definition map)
4. If yes, allows the request; if no, returns 403 with error code
   `AUTH_INSUFFICIENT_PERMISSION` and a generic detail message:
   `"Insufficient permissions"` (replaces the current
   `AUTH_INSUFFICIENT_ROLE` code — since we are in spec phase with no
   deployed instances, this is not a breaking change)

The 403 response MUST NOT disclose which capability was required. A
generic message prevents information leakage about the internal
authorization model.

Scope is applied separately as a query filter (not at the endpoint level)
by `confidential_ticket_filter()` and similar scope-aware filters.

### Authorization chain evaluation order

For ticket endpoints that are capability-protected and operate on a
specific ticket, the authorization chain evaluates in this order:

1. **Authentication** (`get_current_user`) — extract the current user
   from the session/token. Returns 401 if not authenticated.
2. **Capability** (`require_capability`) — check the user has the
   required capability. Returns 403 `AUTH_INSUFFICIENT_PERMISSION` if
   not. This check is user-level (does not depend on the specific
   ticket), so it does not leak information about ticket existence.
3. **Ticket accessibility** (`require_accessible_ticket`) — check that
   the ticket exists, is visible to the caller (scope + grant +
   bugowner), and is not soft-deleted (unless caller has
   `admin_ticket_ops` capability). Returns 404 for invisible tickets,
   410 for soft-deleted tickets.
4. **Mutability guard** (`require_ticket_mutable`) — check that the
   ticket is in a mutable state (not in a final status). Returns 409
   if the ticket cannot be modified.

This ordering is security-significant: the capability check (step 2)
fires before the accessibility check (step 3). A user without the
required capability receives 403 regardless of whether the ticket
exists — this prevents probing for ticket existence via differentiated
error codes.

For non-ticket endpoints (user management, settings, fetchers), only
steps 1 and 2 apply.

### Conditional capability checks

Some endpoints are Public but accept optional parameters that require a
capability. For example, `GET /api/v1/tickets` is Public, but the
`include_deleted` query parameter requires `admin_ticket_ops` capability.
In these cases, the capability check is performed inline in the handler
(not via the `require_capability()` dependency) only when the parameter
is present. If the caller lacks the required capability, the parameter
is **silently ignored** — the endpoint returns results as if the
parameter were not provided. The endpoint never returns 403 for a
missing query parameter on a Public or Authenticated endpoint; 403 is
reserved for capability-protected endpoints where the caller cannot
access the endpoint itself.

When a parameter is silently ignored due to insufficient capability, the
backend SHOULD emit a DEBUG-level log entry recording the caller
identity and the ignored parameter name. This preserves the silent
client-facing behavior while enabling security monitoring. The log MUST
NOT include the parameter value to avoid log injection.

## Business Rules

### Assignment target constraint (role-based)

The assignment target constraint remains **role-based**, not
capability-based. Only users holding the `vulnerability_analyst` role can
be assigned as ticket owners. Users with only `automation_agent` (or any
other role) are not valid assignment targets. This is a business rule
("who can own a ticket"), not an authorization check ("who can invoke the
assign endpoint").

- The `triage_ticket` capability controls who can *perform* the
  assignment operation
- The `vulnerability_analyst` role requirement controls who can *be the
  target* of the assignment

### Auto-assignment

The existing auto-assignment rule ("when a user modifies an unassigned
ticket, the ticket is auto-assigned to the acting user") applies **only
if the acting user holds the `vulnerability_analyst` role**. If the
acting user holds only `automation_agent` (or any other role without VA),
auto-assignment is skipped — the bot performs the operation but the
ticket remains unassigned for a human VA to claim.

This ensures bots never become ticket assignees, which would block
human triage workflows.

### Reopen and revert-duplicate assignment

The existing ticket flows for reopen and revert-duplicate embed an
explicit reassignment: the ticket is assigned to the user who performed
the operation. Since the assignment target constraint requires the
`vulnerability_analyst` role, a user who holds only `automation_agent`
(or any non-VA role) cannot be the assignment target.

When a non-VA user performs reopen or revert-duplicate (they have the
`triage_ticket` capability to do so), the reassignment step is skipped
— the ticket retains its current assignee. If the ticket was
unassigned, it remains unassigned.

This is consistent with the auto-assignment rule: bots can trigger
status transitions but never become ticket owners. The audit trail
records the bot as the actor of the status change, which is correct
— the bot performed the operation.

### Confidential ticket creation

The `is_confidential` field in the `POST /api/v1/tickets` request body
requires the `manage_confidentiality` capability. A user with only
`create_ticket` (e.g., `automation_agent`) can create tickets but cannot
set `is_confidential: true`. If the field is present and the caller
lacks `manage_confidentiality`, the endpoint returns 403 with error code
`AUTH_INSUFFICIENT_PERMISSION` and the same generic detail message
`"Insufficient permissions"` used by `require_capability()`. The
response MUST NOT disclose which specific capability was missing — from
the client's perspective, this is indistinguishable from a primary
capability check failure.

This prevents a bot from creating a confidential ticket it cannot
subsequently access (its scope is `non_confidential` and no grant would
exist for it). A VA creating a ticket can set `is_confidential: true`
because they have both `create_ticket` and `manage_confidentiality`.

### Mark-as-duplicate assignment

The mark-as-duplicate flow embeds an assignment step: if the ticket has
no assignee, "the acting VA becomes the assignee." Since the assignment
target constraint requires the `vulnerability_analyst` role, a user who
holds only `automation_agent` (or any non-VA role) cannot be the
assignment target.

When a non-VA user performs mark-as-duplicate (they have the
`triage_ticket` capability to do so), the embedded assignment step is
skipped — the ticket retains its current assignee. If the ticket was
unassigned, it remains unassigned.

This is consistent with the auto-assignment, reopen, and
revert-duplicate rules: bots can trigger status transitions but never
become ticket owners.

### Status transitions table update

The status transitions table in `tickets.md` uses the "Who" column to
describe the actor of each transition. Some entries say "Any VA", others
say "Assignee" (e.g., Analysis → Ignored). However, the API endpoint
definitions do not enforce the "Assignee" constraint — the ignore
endpoint has access level "Vulnerability Analyst" (any VA) with no
assignee identity check in its error responses. The "Who" column
appears to describe the **typical** actor, not an enforced constraint.

With capability-based access, the "Who" column must be updated to
reflect capabilities:

- "Any VA" → `triage_ticket`
- "Assignee" (Analysis → Ignored) → `triage_ticket` (no assignee
  enforcement exists at the API level)

The "Trigger" column also uses "VA" in descriptions (e.g., "VA clicks
'Ignore' action"). These should be updated to generic language (e.g.,
"User clicks 'Ignore' action") since `automation_agent` users also
perform these operations.

Transitions where the actor includes an embedded assignment step
(revert-duplicate, reopen) should note that the assignment applies
only if the actor holds the `vulnerability_analyst` role.

### AD role mapping for `automation_agent`

AD groups can be mapped to `automation_agent` via `RoleMapping`. This is
a valid use case — for example, a team of humans who should perform
ticket operations but must not access embargoed (confidential) data. The
admin is responsible for ensuring the mapping is intentional.

### Bot account bootstrap

Bot accounts are local users (no AD identity) created via CLI. The
recommended bootstrap sequence:

```
sentinel manage-user create --username mybot --email mybot@example.com --role automation_agent
```

The `create` command prompts for a password interactively. After
creation, authenticate with the bot credentials to create an API key.
Bots should use API keys exclusively for ongoing operations (not
session-based authentication). The initial password is used only once to
create the first API key via `POST /api/v1/api-keys`.

API key rotation is the admin's responsibility. There is no automatic
rotation schedule — the admin creates a new key and revokes the old one
when needed.

### Role wire format

All role values in API requests and responses use lowercase with
underscores, consistent with the existing convention:

- `admin`
- `vulnerability_analyst`
- `automation_agent`

This applies to all endpoints that accept or return role values:
`POST /api/v1/admin/users/{user}/roles`, `GET /api/v1/users/{user}`,
CLI `--role` parameter, and `RoleMapping` API payloads.

## Feature Spec Endpoint Declarations

Feature specifications that define API endpoints will change their access
declarations from role-based to capability-based:

**Before:**
```markdown
**Access**: Vulnerability Analyst
```

**After:**
```markdown
**Capability**: `create_ticket`
```

Endpoints that require no capability keep the existing format:

```markdown
**Access**: Public
```

```markdown
**Access**: Authenticated
```

---

## Implementation Plan

All work is specification-only — no code implementation, no migrations.

### Phase 1 — Rewrite `docs/features/identity/rbac.md`

The RBAC specification is the authoritative source for the authorization
model. This is the core deliverable.

Changes:

- [ ] Define `Capability` enum with all capabilities and their covered operations
- [ ] Define `Scope` enum with values and semantics
- [ ] Define static role definition map (`Role → {Capability[], Scope}`)
- [ ] Add `automation_agent` to the Role enum
- [ ] Rewrite Permission Matrix to reflect capabilities (not role checkmarks)
- [ ] Rewrite Endpoint Permission Map: "Access" column becomes "Capability"
      for protected endpoints (Public/Authenticated remain unchanged)
- [ ] Update "Implementation Details > Permission Checking" section: from
      `require_role()` to `require_capability()`, explain scope filtering
- [ ] Update "Data Model" section: Role enum with new value, note that
      capability/scope map is static in code (no DB tables)
- [ ] Add design note on multi-role resolution (union of capabilities,
      least restrictive scope)
- [ ] Add design note on scope vs TicketAccessGrant interaction
- [ ] Update "Business Rules" — existing rules remain, add:
  - Assignment target constraint stays role-based (VA role required)
  - Auto-assignment skips non-VA users (bots never become assignees)
  - Multi-role scope resolution is union (admin responsibility)
  - Zero-role users have effective scope `non_confidential`
- [ ] Rename `AUTH_INSUFFICIENT_ROLE` to `AUTH_INSUFFICIENT_PERMISSION`
- [ ] Document conditional capability checks for Public endpoints with
      privileged parameters (silently ignored convention)
- [ ] Document authorization chain evaluation order (authentication →
      capability → ticket accessibility → mutability)
- [ ] Update Business Rule 8: zero-role users have scope
      `non_confidential` but can access specific confidential tickets via
      `TicketAccessGrant` or bugowner (unlike unauthenticated users)
- [ ] Add business rule: reopen/revert-duplicate/mark-as-duplicate skip
      reassignment for non-VA users (ticket retains current assignee)
- [ ] Add business rule: confidential ticket creation requires
      `manage_confidentiality` — 403 if caller lacks this capability
- [ ] Add bot account bootstrap example (CLI create + API key workflow)
- [ ] Document role wire format (`automation_agent` serialized as
      lowercase with underscores, consistent with existing roles)
- [ ] Document scope resolution rule: if any role has `all`, effective
      scope is `all`; otherwise `non_confidential`. No total order
      constraint — future scope values will define their own resolution
- [ ] Add design note: scope is API-layer only (background tasks bypass)
- [ ] Document 403 response body: generic `"Insufficient permissions"`
      message — MUST NOT disclose the required capability name

### Phase 2 — Update `docs/data-model.md`

- [ ] Add `automation_agent` to the `Role` enum values
- [ ] Document that the capability/scope map is static in code, not stored in DB
- [ ] Update User table description: replace "A user with no roles has
      the same access as an unauthenticated user" with the corrected
      distinction (zero-role users can access confidential tickets via
      `TicketAccessGrant` or bugowner, unlike unauthenticated users)
- [ ] No new tables required

### Phase 3 — Update `docs/api-spec.md`

- [ ] Update authorization conventions section to describe capability-based
      access control
- [ ] Document the three access levels: Public, Authenticated, Capability-protected
- [ ] Document that scope is applied as an implicit query filter, not as an
      endpoint-level check
- [ ] Rename error code `AUTH_INSUFFICIENT_ROLE` to `AUTH_INSUFFICIENT_PERMISSION`
- [ ] Update `require_accessible_ticket` specification to use scope-based
      logic instead of role-based `caller_is_privileged`
- [ ] Update `require_accessible_ticket` soft-delete check: use
      `admin_ticket_ops` capability instead of Admin role
- [ ] Document endpoint declaration field naming convention: `Access` for
      Public/Authenticated, `Capability` for capability-protected
- [ ] Document the "silently ignored" convention for conditional capability
      checks on Public/Authenticated endpoints, including the DEBUG-level
      logging recommendation
- [ ] Document authorization chain evaluation order
- [ ] Update Global Responses table: rename `AUTH_INSUFFICIENT_ROLE` to
      `AUTH_INSUFFICIENT_PERMISSION` and change dependency reference from
      `require_role` to `require_capability`
- [ ] Update Error Code Categories table: rename `AUTH_INSUFFICIENT_ROLE`
      to `AUTH_INSUFFICIENT_PERMISSION` in the `AUTH_*` listing

### Phase 4 — Update `docs/conventions.md`

- [ ] Update "FastAPI Conventions" section: `require_capability()` replaces
      `require_role()` as the standard authorization dependency
- [ ] Add example of capability-based endpoint declaration
- [ ] Note that scope filtering is handled by shared query utilities, not
      per-endpoint logic

### Phase 5 — Update `docs/features/tickets/tickets.md`

This is the most impacted feature spec due to confidentiality rules.

- [ ] Explicitly retire Authorization Rule #1 (role-based: VA or Admin) and
      replace with scope-based rule ("user's effective scope is `all`") —
      remove the old rule, do not leave both in place
- [ ] Update `confidential_ticket_filter()` specification: replace
      `caller_is_privileged: bool` with `caller_scope: Scope | None`
      (other parameters unchanged)
- [ ] Update all endpoint declarations from "Access: Vulnerability Analyst"
      to "Capability: X"
- [ ] Ensure TicketAccessGrant override behavior is clearly documented
      (visibility only, write requires capability)
- [ ] Update auto-assignment rule: skip for users without VA role
- [ ] Update assignment target constraint: explicitly role-based (VA only)
- [ ] Add reopen/revert-duplicate rule: reassignment step is skipped for
      non-VA users (ticket retains current assignee)
- [ ] Update duplicate target resolution: note accepted risk applies equally
      to `automation_agent` users
- [ ] Add confidential ticket creation rule: `is_confidential: true`
      requires `manage_confidentiality` — rejected with 403 if the caller
      lacks this capability
- [ ] Confirm that mutation endpoints on invisible tickets return 404
      (not 403) — consistent with the existing invisible-ticket pattern
- [ ] Remove the "inherently safe" claim (auto-assignment is safe
      because only VAs can perform modifying operations) — this becomes
      false with `automation_agent`
- [ ] Update `confidential_ticket_filter()` pseudocode block (not just
      the function signature): replace `IF caller_is_privileged` branch
      with `IF caller_scope == Scope.ALL`
- [ ] Replace all occurrences of "privileged" in the confidentiality
      context with scope-based terminology
- [ ] Update "Who" column in status transitions table: replace "Any VA"
      with `triage_ticket`, replace "Assignee" (Analysis → Ignored) with
      `triage_ticket` (no assignee enforcement exists at the API level).
      Transitions with embedded assignment (revert-duplicate, reopen)
      should note assignment applies only if actor holds VA role
- [ ] Update "Trigger" column in status transitions table: replace "VA"
      with "User" in descriptions (e.g., "VA clicks 'Ignore' action" →
      "User clicks 'Ignore' action") since `automation_agent` users also
      perform these operations
- [ ] Replace "VA" with "user" or "authenticated user" throughout the
      spec where the intent is "any user with capabilities" rather than
      "a user holding the VA role specifically"
- [ ] Update `include_deleted` parameter description and soft-deletion
      visibility note: replace "Admin role" with `admin_ticket_ops`
      capability

#### Update `docs/features/tickets/ticket-mutations.md`

The service-layer function contracts must be updated to handle non-VA
actors introduced by the `automation_agent` role. Each function performs
the VA role check internally (loading the acting user's roles from the
`User.roles` relationship, see Design Decision "VA role check in
service-layer functions"):

- [ ] Update `auto_assign_if_needed()`: check if acting user holds the
      `vulnerability_analyst` role before assigning — skip
      auto-assignment if not. Function signature unchanged
- [ ] Update `reopen_from_ignored()`: check acting user's VA role
      before reassignment — skip reassignment if not VA (ticket retains
      current assignee). Function signature unchanged
- [ ] Update `revert_duplicate()`: check acting user's VA role before
      reassignment — skip reassignment if not VA (ticket retains current
      assignee). Function signature unchanged (`acting_user_id` remains
      required for audit trail, but assignment behavior is conditional)
- [ ] Update `mark_as_duplicate()`: check acting user's VA role before
      the embedded assignment step (step 8) — skip if not VA. Function
      signature unchanged

### Phase 6 — Update remaining feature specs with endpoints

For each file below, replace role-based access declarations with
capability-based declarations on protected endpoints. Public and
Authenticated endpoints remain unchanged.

Identity specs:
- [ ] `docs/features/identity/authentication.md`
- [ ] `docs/features/identity/local-authentication.md`
- [ ] `docs/features/identity/sso-authentication.md`
- [ ] `docs/features/identity/user-management.md` — also update CLI
      `--role` parameter to accept `automation_agent` in `manage-user`
      commands (`create`, `update`, `list --role`)
- [ ] `docs/features/identity/ad-integration.md` — note that AD groups
      can be mapped to `automation_agent`; also update Business Rule 2
      (stale echo of rbac.md Business Rule 8: "A user with no roles has
      the same access as an unauthenticated user") to reflect the
      corrected distinction between zero-role and unauthenticated users
- [ ] `docs/features/identity/identity-audit-log.md`

Ticket specs:
- [ ] `docs/features/tickets/cvss-scoring.md`
- [ ] `docs/features/tickets/ticket-audit-log.md`
- [ ] `docs/features/tickets/ticket-references.md`

Package specs:
- [ ] `docs/features/packages/package-model.md` — also update
      `include_deleted` business rule from "Admin role" to
      `admin_ticket_ops` capability check
- [ ] `docs/features/packages/product-catalog.md`
- [ ] `docs/features/packages/maintainer.md`
- [ ] `docs/features/packages/ibs-submission-tracking.md`

Platform specs:
- [ ] `docs/features/platform/system-settings.md`
- [ ] `docs/features/platform/fetcher-operations.md`
- [ ] `docs/features/platform/fetcher-infrastructure.md`
- [ ] `docs/features/platform/audit-trail-infrastructure.md`

### Phase 7 — Update cross-cutting documentation

- [ ] `docs/architecture.md` — update "Security Considerations" section if
      it references the RBAC model

### Phase 8 — Reviewers

After all specification changes are complete, run the following reviewers
to verify coherence and completeness:

- [ ] `@spec-coherence-reviewer` on `docs/features/identity/rbac.md` —
      verify no contradictions with other specs
- [ ] `@spec-gap-analyzer` on `docs/features/identity/rbac.md` — verify
      functional completeness of the new model
- [ ] `@data-model-reviewer` on `docs/data-model.md` changes — verify
      simplicity and convention adherence
- [ ] `@docs-reviewer` — verify documentation completeness and coherence
      across all modified files
- [ ] `@docs-placement-reviewer` — verify that capability/scope rules are
      in the correct location (rbac.md, not scattered)
- [ ] `@api-convention-reviewer` on each feature spec with modified
      endpoint declarations — verify API convention conformity
- [ ] `@security-reviewer` on the RBAC and confidentiality changes —
      verify no security gaps in the scope model

### Phase 9 — Remove draft

- [ ] Delete `docs/drafts/capability-scope-rbac.md`

---

## Design Decisions (resolved)

### TicketAccessGrant + `non_confidential` scope

**Decision**: The grant overrides the scope. A user with `non_confidential`
scope can access a specific confidential ticket if they have an explicit
`TicketAccessGrant` for it. The grant provides full access (read and
write) — no read-only vs read-write distinction. Rationale: this allows
VAs to deliberately share specific confidential tickets with bots when
needed for automation, and keeps the model simple.

### Authenticated and Anonymous as roles

**Decision**: Not needed. These are access levels (authentication state),
not authorization profiles. They do not carry capabilities or scope.

### Multi-role scope resolution

**Decision**: Union (least restrictive). If an admin assigns both
`vulnerability_analyst` and `automation_agent` to the same user, the
effective scope is `all`. This is the admin's responsibility — no
mutual exclusivity enforcement or warnings. Rationale: adding guards
increases complexity for a scenario that is simply an admin
configuration mistake, correctable by removing the extra role.

### Effective scope for zero-role users

**Decision**: Authenticated users with no roles have an effective scope
of `non_confidential`. They can still access specific confidential
tickets via `TicketAccessGrant` or bugowner matching. Unauthenticated
users have `caller_scope = None` — they see only non-confidential
tickets with no grant/bugowner checks.

### Auto-assignment for `automation_agent`

**Decision**: Auto-assignment is skipped for users who do not hold the
`vulnerability_analyst` role. Bots perform operations on tickets but
never become assignees. Rationale: ticket ownership implies human
accountability in the triage workflow.

### Assignment target constraint

**Decision**: Remains role-based. Only users with the
`vulnerability_analyst` role can be assignment targets. This is a
business rule (who can own tickets), not an authorization check (who can
invoke the endpoint). The `triage_ticket` capability controls who can
perform the assignment; the VA role controls who can be the target.

### AD group mapping to `automation_agent`

**Decision**: Allowed. AD groups can be mapped to `automation_agent` via
`RoleMapping`. Valid use case: a team of humans who should perform ticket
operations but must not access embargoed data.

### `AUTH_INSUFFICIENT_ROLE` error code

**Decision**: Renamed to `AUTH_INSUFFICIENT_PERMISSION`. Since we are in
spec phase with no deployed instances, this is not a breaking change.

### Conditional capability checks on Public endpoints

**Decision**: Public endpoints that accept parameters requiring elevated
access (e.g., `include_deleted` on `GET /api/v1/tickets`) perform
capability checks inline in the handler, not via the
`require_capability()` dependency. If the caller lacks the capability,
the parameter is **silently ignored** — the endpoint returns results as
if the parameter were not provided. 403 is never returned for a missing
query parameter on a Public or Authenticated endpoint.

### Scope is API-layer only

**Decision**: Scope is enforced only at the API layer — via
`confidential_ticket_filter()` in query endpoints and
`require_accessible_ticket` in single-ticket endpoints. It does not
apply to the service layer or background tasks. Celery workers,
fetchers, and event consumers continue to process all tickets
(including confidential ones) without scope restrictions. Scope is an
access-control concept, not a data-partitioning concept.

### Retirement of role-based confidentiality access

**Decision**: The existing Authorization Rule #1 in `tickets.md` —
"Role-based: The user holds the `Vulnerability Analyst` or `Admin`
role" — is **retired** and replaced by the scope check: "The user's
effective scope is `all`". This is semantically equivalent today
(only VA and Admin have scope `all`), but the mechanism changes from
role enumeration to scope evaluation. Phase 5 must explicitly
replace this rule in `tickets.md` to prevent implementers from
applying both checks (role AND scope), which would create a
redundant or conflicting condition.

### Soft-delete visibility uses capability, not role

**Decision**: The `require_accessible_ticket` soft-delete check — "if
`deleted_at IS NOT NULL` and the caller does not hold the Admin role,
return 410" — is updated to use the `admin_ticket_ops` capability
instead of the Admin role directly. This ensures consistency with the
capability model: any future role that includes `admin_ticket_ops`
would also see soft-deleted tickets.

### Endpoint declaration field naming

**Decision**: Feature specs use two distinct field names to declare
endpoint authorization:

- `**Access**: Public` or `**Access**: Authenticated` — for endpoints
  that check authentication state only (no capability required)
- `**Capability**: <capability_name>` — for endpoints that require a
  specific capability

The intentional field name change serves as a visual indicator: `Access`
means "authentication level only", `Capability` means "specific
authorization check required". This distinction helps reviewers and
implementers immediately identify the authorization model of each
endpoint. The format is defined in `api-spec.md` (updated in Phase 3).

### Duplicate target resolution and `automation_agent`

**Decision**: The existing accepted risk for duplicate target resolution
— where `resolve_canonical_target` traverses the `duplicate_of_id`
chain at the service layer without confidentiality checks — applies
equally to `automation_agent` users. The canonical resolver may expose
the `SNTL-{n}` identifier of a confidential ticket in the API response
when the canonical target is confidential. This is the same accepted
risk documented in `tickets.md` for VA operations. The risk profile is
noted as slightly different for bots (which may expose data in automated
reports), but the mitigation is the same: the bot only reaches this
code path if it has `triage_ticket` capability and visibility on the
source ticket.

### TicketAccessGrant: visibility only, not capability elevation

**Decision**: Visibility and write capability are independent checks.
`TicketAccessGrant` grants **visibility only** — the ability to see a
confidential ticket that would otherwise be hidden by the user's scope.
Write access requires capabilities (from roles). A zero-role user with
a grant can read the ticket but cannot modify it. An `automation_agent`
with a grant can read and modify the ticket because they have
capabilities like `triage_ticket` and `manage_packages`. The grant does
not elevate capabilities — it only overrides the scope restriction for
that specific ticket. This is by design: the VA deliberately shares a
specific ticket with a bot for automation purposes.

### Capability granularity: coarse grouping

**Decision**: Keep the coarse grouping (~11 capabilities). The current
three roles (`admin`, `vulnerability_analyst`, `automation_agent`) are
well served by grouped capabilities. There is no foreseeable use case
for partial grants within a group (e.g., "can assign but not change
status"). Fine-grained capabilities (~22) would yield a ~7:1
capability-to-role ratio — over-engineering for the current role count.

Splitting a coarse capability later is a bounded, mechanical refactor
(~12 endpoint decorators + tests for the two largest groups), not an
architectural change. The cost is mitigated by grepping
`require_capability` call sites and parametrized authorization tests.
AD role mappings are unaffected (they map to roles, not capabilities).

The capability groupings may be refined in the future if a new role
requires a subset of operations within an existing group. This does not
require any architectural change — only adding new enum values,
updating the role definition map, and replacing decorators on the
affected endpoints.

### Confidential ticket creation requires `manage_confidentiality`

**Decision**: The `is_confidential` field in `POST /api/v1/tickets` is
a confidentiality mutation, not a ticket creation parameter. It requires
`manage_confidentiality` in addition to `create_ticket`. Without this
capability, the field is rejected with 403 (not silently ignored —
unlike query parameters, a request body field on a mutation endpoint
represents explicit intent, and silent degradation would be misleading).
This prevents `automation_agent` users from creating orphaned
confidential tickets they cannot subsequently access.

### Mark-as-duplicate assignment skip for non-VA users

**Decision**: When a user with `triage_ticket` capability but without
the `vulnerability_analyst` role performs mark-as-duplicate on an
unassigned ticket, the embedded assignment step is skipped. The ticket
retains its current assignee (or remains unassigned). This is consistent
with auto-assignment, reopen, and revert-duplicate: non-VA users can
trigger status transitions but cannot become ticket owners.

### Reopen/revert-duplicate for non-VA users

**Decision**: When a user with `triage_ticket` capability but without
the `vulnerability_analyst` role performs reopen or revert-duplicate,
the embedded reassignment step is skipped. The ticket retains its
current assignee (or remains unassigned). This is consistent with the
auto-assignment and assignment target rules: non-VA users can trigger
status transitions but cannot become ticket owners.

### VA role check in service-layer functions

**Decision**: The VA role check required by `auto_assign_if_needed()`,
`reopen_from_ignored()`, `revert_duplicate()`, and
`mark_as_duplicate()` is performed **internally** within each function,
not via a `bool` parameter passed by the caller. The function loads
the acting user's roles from the `User.roles` relationship (already
in memory if the user was loaded with `selectinload`) and checks for
`Role.VULNERABILITY_ANALYST`. This keeps the function signatures
simple and avoids propagating a role-awareness parameter through the
entire call chain (API → service → helper). A single-row lookup on
`UserRole` is not an "expensive query" under the transaction hygiene
rules — it is a fast indexed read, not an aggregation or analytical
query.

### Zero-role users and Business Rule 8

**Decision**: The current `rbac.md` Business Rule 8 ("A user with no
roles has the same access as an unauthenticated user") is inaccurate
and predates the `TicketAccessGrant` and bugowner visibility
mechanisms. Under the capability + scope model, zero-role users have:

- Effective scope: `non_confidential` (same default visibility as
  unauthenticated)
- Per-ticket visibility: can access specific confidential tickets via
  `TicketAccessGrant` or bugowner matching (unlike unauthenticated
  users)
- Capabilities: none (cannot modify any data)

Business Rule 8 must be updated in Phase 1 to reflect this distinction.

## Open Points

No open points remain. All decisions have been resolved.

## Cross-references

- `docs/features/identity/rbac.md` — authoritative RBAC specification
- `docs/features/tickets/tickets.md` — confidentiality rules
- `docs/features/tickets/ticket-mutations.md` — service-layer mutation contracts
- `docs/features/identity/ad-integration.md` — AD sync, role mappings
- `docs/data-model.md` — Role enum, UserRole table
- `docs/api-spec.md` — API authorization conventions
- `docs/conventions.md` — FastAPI implementation conventions
