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
   `AUTH_INSUFFICIENT_PERMISSION` (replaces the current
   `AUTH_INSUFFICIENT_ROLE` code — since we are in spec phase with no
   deployed instances, this is not a breaking change)

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
      privileged parameters (silently ignored convention)
- [ ] Document authorization chain evaluation order (authentication →
      capability → ticket accessibility → mutability)
- [ ] Update Business Rule 8: zero-role users have scope
      `non_confidential` but can access specific confidential tickets via
      `TicketAccessGrant` or bugowner (unlike unauthenticated users)
- [ ] Add business rule: reopen/revert-duplicate skip reassignment for
      non-VA users (ticket retains current assignee)
- [ ] Add design note: scope is API-layer only (background tasks bypass)

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
- [ ] Update `require_accessible_ticket` soft-delete check: use
      `admin_ticket_ops` capability instead of Admin role
- [ ] Document endpoint declaration field naming convention: `Access` for
      Public/Authenticated, `Capability` for capability-protected
- [ ] Document the "silently ignored" convention for conditional capability
      checks on Public/Authenticated endpoints
- [ ] Document authorization chain evaluation order

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

### TicketAccessGrant provides visibility, not write access

**Decision**: `TicketAccessGrant` grants **visibility only** — the
ability to see a confidential ticket that would otherwise be hidden by
the user's scope. Write access requires capabilities (from roles).
A zero-role user with a grant can read the ticket but cannot modify it.
An `automation_agent` with a grant can read and modify the ticket
because they have capabilities like `triage_ticket` and
`manage_packages`. The grant does not elevate capabilities — it only
overrides the scope restriction for that specific ticket. This is by
design: the VA deliberately shares a specific ticket with a bot for
automation purposes.

### Reopen/revert-duplicate for non-VA users

**Decision**: When a user with `triage_ticket` capability but without
the `vulnerability_analyst` role performs reopen or revert-duplicate,
the embedded reassignment step is skipped. The ticket retains its
current assignee (or remains unassigned). This is consistent with the
auto-assignment and assignment target rules: non-VA users can trigger
status transitions but cannot become ticket owners.

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

### Capability granularity: coarse now or fine-grained from the start?

The current draft defines ~11 grouped capabilities (e.g., `triage_ticket`
bundles assign, change status, mark duplicate, associate CVE, and set
severity). An alternative is to define finer-grained capabilities from
the start (e.g., separate `assign_ticket`, `change_ticket_status`,
`associate_cve`, `set_severity`).

**What splitting a capability later costs:**

- **Spec changes**: the capability enum gains new values, the role
  definition map is updated, and the Endpoint Permission Map rows that
  referenced the old capability are split. Moderate effort (~1 spec
  session).
- **Code changes**: every `require_capability(Cap.TRIAGE_TICKET)` call
  site must be reviewed and replaced with the appropriate fine-grained
  capability. The number of call sites equals the number of endpoints
  that used the old capability. Mechanical but error-prone — missing one
  site silently breaks authorization.
- **Test changes**: every test that sets up a role with the old
  capability must be updated. Parametrized tests over capabilities need
  new cases.
- **Role backward compatibility**: existing roles that had the coarse
  capability must receive all the fine-grained capabilities it was split
  into, to avoid regressions. If the split is done in production, this
  requires a data migration (or seed update) to maintain equivalence.
- **AD role mappings**: unaffected (they map to roles, not capabilities).

**Arguments for coarse now:**

- Simpler to reason about and maintain with 3 roles
- No foreseeable use case today for "can assign but not change status"
- Splitting later is a bounded, mechanical refactor — not an
  architectural change
- YAGNI: fine-grained capabilities that are always granted together
  add complexity without value

**Arguments for fine-grained from the start:**

- Splitting later touches code, tests, and potentially production data
  — doing it now (spec phase, no code, no data) has zero migration cost
- More fine-grained capabilities give maximum flexibility for future
  roles without any spec/code refactoring
- The Permission Matrix already defines operations at roughly this
  granularity level (~20 rows), so the mapping is natural

**Decision**: deferred — to be resolved before Phase 1 begins.

## Cross-references

- `docs/features/identity/rbac.md` — authoritative RBAC specification
- `docs/features/tickets/tickets.md` — confidentiality rules
- `docs/data-model.md` — Role enum, UserRole table
- `docs/api-spec.md` — API authorization conventions
- `docs/conventions.md` — FastAPI implementation conventions
