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
| `manage_users` | Update user fields, manage user roles, reset password, deactivate/reactivate, unlock, view deactivation impact, view/revoke all API keys, view identity audit log |
| `manage_role_mappings` | AD role mapping CRUD, preview role mapping |
| `manage_settings` | View/update system settings, view settings audit log |
| `manage_fetchers` | Trigger manual fetcher run, enable/disable fetchers, modify fetcher config, view fetcher audit log, view error tracebacks |
| `admin_ticket_ops` | Remove CVE from ticket, soft-delete/restore tickets, view deleted tickets |

### Scope Enum

| Scope | Meaning |
|---|---|
| `all` | Unrestricted — all tickets visible including confidential |
| `non_confidential` | Confidential tickets are invisible by default (see Access Grant Override below) |

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

The `confidential_ticket_filter()` function signature changes from:

```python
def confidential_ticket_filter(
    ...,
    caller_is_privileged: bool,     # True if VA or Admin
    ...
)
```

to:

```python
def confidential_ticket_filter(
    ...,
    caller_scope: Scope | None,     # None for unauthenticated
    ...
)
```

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
   `AUTH_INSUFFICIENT_PERMISSION` (replaces the current
   `AUTH_INSUFFICIENT_ROLE` code — since we are in spec phase with no
   deployed instances, this is not a breaking change)

Scope is applied separately as a query filter (not at the endpoint level)
by `confidential_ticket_filter()` and similar scope-aware filters.

### Conditional capability checks

Some endpoints are Public but accept optional parameters that require a
capability. For example, `GET /api/v1/tickets` is Public, but the
`include_deleted` query parameter requires `admin_ticket_ops` capability.
In these cases, the capability check is performed inline in the handler
(not via the `require_capability()` dependency) only when the parameter
is present. If the caller lacks the capability, the parameter is ignored
or returns 403.

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

### AD role mapping for `automation_agent`

AD groups can be mapped to `automation_agent` via `RoleMapping`. This is
a valid use case — for example, a team of humans who should perform
ticket operations but must not access embargoed (confidential) data. The
admin is responsible for ensuring the mapping is intentional.

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
      privileged parameters (e.g., `include_deleted`)

### Phase 2 — Update `docs/data-model.md`

- [ ] Add `automation_agent` to the `Role` enum values
- [ ] Document that the capability/scope map is static in code, not stored in DB
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

### Phase 4 — Update `docs/conventions.md`

- [ ] Update "FastAPI Conventions" section: `require_capability()` replaces
      `require_role()` as the standard authorization dependency
- [ ] Add example of capability-based endpoint declaration
- [ ] Note that scope filtering is handled by shared query utilities, not
      per-endpoint logic

### Phase 5 — Update `docs/features/tickets/tickets.md`

This is the most impacted feature spec due to confidentiality rules.

- [ ] Update confidentiality authorization rules to use scope-based language
- [ ] Update `confidential_ticket_filter()` specification (scope parameter)
- [ ] Update all endpoint declarations from "Access: Vulnerability Analyst"
      to "Capability: X"
- [ ] Ensure TicketAccessGrant override behavior is clearly documented
      (read-write access, scope + capability independence)
- [ ] Update auto-assignment rule: skip for users without VA role
- [ ] Update assignment target constraint: explicitly role-based (VA only)
- [ ] Confirm that mutation endpoints on invisible tickets return 404
      (not 403) — consistent with the existing invisible-ticket pattern

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
      can be mapped to `automation_agent`
- [ ] `docs/features/identity/identity-audit-log.md`

Ticket specs:
- [ ] `docs/features/tickets/cvss-scoring.md`
- [ ] `docs/features/tickets/ticket-audit-log.md`
- [ ] `docs/features/tickets/ticket-references.md`

Package specs:
- [ ] `docs/features/packages/package-model.md`
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

### TicketAccessGrant visibility vs write access

**Decision**: Visibility and write capability are independent checks.
A user with no roles but a `TicketAccessGrant` can see the ticket but
cannot modify it (no capabilities). A user with `automation_agent` role
and a grant can see and modify the ticket (capabilities + visibility).

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
the parameter is ignored or returns 403.

## Cross-references

- `docs/features/identity/rbac.md` — authoritative RBAC specification
- `docs/features/tickets/tickets.md` — confidentiality rules
- `docs/data-model.md` — Role enum, UserRole table
- `docs/api-spec.md` — API authorization conventions
- `docs/conventions.md` — FastAPI implementation conventions
