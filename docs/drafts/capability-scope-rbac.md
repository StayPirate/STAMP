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
  scope is `all`).

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
that specific ticket only.

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

### Current (RBAC puro)

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
4. If yes, allows the request; if no, returns 403

Scope is applied separately as a query filter (not at the endpoint level)
by `confidential_ticket_filter()` and similar scope-aware filters.

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
- [ ] Update "Business Rules" — existing rules remain, add any new rules
      for automation_agent (e.g., cannot be assignment target per existing
      assignment constraint: target must hold VA role)

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

### Phase 6 — Update remaining feature specs with endpoints

For each file below, replace role-based access declarations with
capability-based declarations on protected endpoints. Public and
Authenticated endpoints remain unchanged.

Identity specs:
- [ ] `docs/features/identity/authentication.md`
- [ ] `docs/features/identity/local-authentication.md`
- [ ] `docs/features/identity/sso-authentication.md`
- [ ] `docs/features/identity/user-management.md`
- [ ] `docs/features/identity/ad-integration.md`
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

## Open Questions (resolved)

### TicketAccessGrant + `non_confidential` scope

**Decision**: The grant overrides the scope. A user with `non_confidential`
scope can access a specific confidential ticket if they have an explicit
`TicketAccessGrant` for it. Rationale: this allows VAs to deliberately
share specific confidential tickets with bots when needed for automation.

### Authenticated and Anonymous as roles

**Decision**: Not needed. These are access levels (authentication state),
not authorization profiles. They do not carry capabilities or scope.

## Cross-references

- `docs/features/identity/rbac.md` — authoritative RBAC specification
- `docs/features/tickets/tickets.md` — confidentiality rules
- `docs/data-model.md` — Role enum, UserRole table
- `docs/api-spec.md` — API authorization conventions
- `docs/conventions.md` — FastAPI implementation conventions
