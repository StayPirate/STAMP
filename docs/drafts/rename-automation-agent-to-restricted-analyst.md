# Rename `automation_agent` to `restricted_analyst`

## Motivation

The current role name `automation_agent` describes the expected consumer of
the role (bots, AI agents) rather than its capability/scope profile. The
other two roles follow a function-based naming convention:

- `admin` — describes what the role does (administer)
- `vulnerability_analyst` — describes the function (analyze vulnerabilities)
- `automation_agent` — describes who uses it (an automation agent)

Renaming to `restricted_analyst` aligns with the convention: the name
communicates that this role provides analyst-level capabilities with
restricted scope (no confidentiality management, non-confidential scope).
The name is neutral with respect to the type of account that holds it.

## Scope

- **Rename string**: `automation_agent` → `restricted_analyst`
- **Rename display name**: `Automation Agent` → `Restricted Analyst`
- **Rephrase descriptions**: remove bot/AI-specific language; describe
  the role in terms of its capabilities and scope restrictions
- **No code changes**: the role is not yet implemented in backend or
  frontend code
- **External file**: `/tmp/opencode/sentinel-architecture-slides.md`
  must also be updated

## Plan

Each step lists the exact text to find (OLD) and replace with (NEW).
Indentation and surrounding context are included to avoid ambiguity.

---

### Step 1 — `docs/features/identity/rbac.md` (13 occurrences)

#### 1a — Role definition table (line 87)

OLD:
```
| `automation_agent` | `create_ticket`, `triage_ticket`, `manage_packages`, `manage_cvss`, `manage_references` | `non_confidential` |
```

NEW:
```
| `restricted_analyst` | `create_ticket`, `triage_ticket`, `manage_packages`, `manage_cvss`, `manage_references` | `non_confidential` |
```

#### 1b — Design note (lines 93-95)

OLD:
```
- `automation_agent` shares all VA capabilities except
  `manage_confidentiality` — bots cannot set the confidentiality flag or
  manage access grants
```

NEW:
```
- `restricted_analyst` shares all VA capabilities except
  `manage_confidentiality` — this role cannot set the confidentiality
  flag or manage access grants
```

#### 1c — Scope vs capability explanation (lines 234-236)

OLD:
```
but cannot modify it — they have no capabilities. An `automation_agent`
with a grant can both see and modify the ticket because they have
```

NEW:
```
but cannot modify it — they have no capabilities. A `restricted_analyst`
with a grant can both see and modify the ticket because they have
```

#### 1d — Auto-assignment rule, point 11 (lines 610-613)

OLD:
```
    holds the `vulnerability_analyst` role. If the acting user holds only
    `automation_agent` (or any other non-VA role), auto-assignment is
    skipped — the bot performs the operation but the ticket remains
    unassigned for a human to claim
```

NEW:
```
    holds the `vulnerability_analyst` role. If the acting user holds only
    `restricted_analyst` (or any other non-VA role), auto-assignment is
    skipped — the operation proceeds but the ticket remains unassigned
    for a vulnerability analyst to claim
```

#### 1e — Status transitions, point 12 (lines 619-620)

OLD:
```
    If the ticket was unassigned, it remains unassigned. Bots can trigger
    status transitions but never become ticket owners
```

NEW:
```
    If the ticket was unassigned, it remains unassigned. Non-VA users can
    trigger status transitions but are never assigned as ticket owners
```

#### 1f — Confidential ticket creation, point 13 (lines 623-628)

OLD:
```
    in addition to `create_ticket`. A user with only `create_ticket` (e.g.,
    `automation_agent`) can create tickets but cannot set
    `is_confidential: true`. If the field is present and the caller lacks
    `manage_confidentiality`, the endpoint returns 403 with error code
    `AUTH_INSUFFICIENT_PERMISSION`. This prevents bots from creating
    confidential tickets they cannot subsequently access.
```

NEW:
```
    in addition to `create_ticket`. A user with only `create_ticket` (e.g.,
    `restricted_analyst`) can create tickets but cannot set
    `is_confidential: true`. If the field is present and the caller lacks
    `manage_confidentiality`, the endpoint returns 403 with error code
    `AUTH_INSUFFICIENT_PERMISSION`. This prevents users without
    `manage_confidentiality` from creating confidential tickets they
    cannot subsequently access.
```

#### 1g — Account bootstrap, point 14 (lines 631-641)

OLD:
```
14. **Bot account bootstrap**: bot accounts are local users created via
    CLI with the `automation_agent` role:

    ```
    sentinel manage-user create --username mybot --email mybot@example.com --role automation_agent
    ```

    The `create` command prompts for a password interactively. After
    creation, authenticate with the bot credentials to create an API key
    via `POST /api/v1/api-keys`. Bots should use API keys exclusively for
    ongoing operations. API key rotation is the admin's responsibility
```

NEW:
```
14. **Restricted analyst account setup**: restricted analyst accounts
    are local users created via CLI with the `restricted_analyst` role:

    ```
    sentinel manage-user create --username mybot --email mybot@example.com --role restricted_analyst
    ```

    The `create` command prompts for a password interactively. After
    creation, authenticate with the account credentials to create an API
    key via `POST /api/v1/api-keys`. Non-interactive accounts should use
    API keys exclusively for ongoing operations. API key rotation is the
    admin's responsibility
```

#### 1h — Wire format (line 701)

OLD:
```
- `automation_agent`
```

NEW:
```
- `restricted_analyst`
```

Note: this appears in a bullet list with `admin` and
`vulnerability_analyst` immediately above. Use surrounding context to
match the correct occurrence.

#### 1i — AD group mapping (lines 717-720)

OLD:
```
AD groups can be mapped to `automation_agent` via `RoleMapping`. This is
a valid use case — for example, a team of humans who should perform
ticket operations but must not access embargoed (confidential) data. The
admin is responsible for ensuring the mapping is intentional.
```

NEW:
```
AD groups can be mapped to `restricted_analyst` via `RoleMapping`. This
is a valid use case — for example, users who should perform ticket
operations but must not access embargoed (confidential) data. The admin
is responsible for ensuring the mapping is intentional.
```

#### 1j — Data model summary, Role enum (line 686)

OLD:
```
- **Role** enum: `Admin`, `Vulnerability Analyst`, `Automation Agent`
```

NEW:
```
- **Role** enum: `Admin`, `Vulnerability Analyst`, `Restricted Analyst`
```

#### 1k — Scope override, bot reference (lines 225-226)

OLD:
```
explicit access to a confidential ticket to a bot with
`non_confidential` scope. The grant overrides the scope restriction for
```

NEW:
```
explicit access to a confidential ticket to a user with
`non_confidential` scope. The grant overrides the scope restriction for
```

#### 1l — Scope override, bot capabilities (lines 228-229)

OLD:
```
write) — the bot can perform any operation its capabilities allow on the
granted ticket. There is no read-only vs read-write distinction in access
```

NEW:
```
write) — the user can perform any operation their capabilities allow on
the granted ticket. There is no read-only vs read-write distinction in access
```

#### 1m — Business rule cross-reference (line 591)

OLD:
```
   For bot accounts, see Business Rule 14
```

NEW:
```
   For restricted analyst accounts, see Business Rule 14
```

---

### Step 2 — `docs/features/tickets/tickets.md` (5 occurrences)

#### 2a — Ticket creation (line 196)

OLD:
```
  (e.g., `automation_agent`):
```

NEW:
```
  (e.g., `restricted_analyst`):
```

#### 2b — Assignment constraint (lines 430-432)

OLD:
```
Auto-assignment checks internally whether the acting user holds the
`vulnerability_analyst` role — if not (e.g., an `automation_agent`),
auto-assignment is skipped and the ticket remains unassigned.
```

NEW:
```
Auto-assignment checks internally whether the acting user holds the
`vulnerability_analyst` role — if not (e.g., a `restricted_analyst`),
auto-assignment is skipped and the ticket remains unassigned.
```

#### 2c — Auto-assignment on unassigned tickets (lines 461-464)

OLD:
```
as the modifying operation. If the acting user does not hold the
`vulnerability_analyst` role (e.g., an `automation_agent`),
auto-assignment is skipped — the ticket remains unassigned for a human
to claim.
```

NEW:
```
as the modifying operation. If the acting user does not hold the
`vulnerability_analyst` role (e.g., a `restricted_analyst`),
auto-assignment is skipped — the ticket remains unassigned for a
vulnerability analyst to claim.
```

#### 2d — Revert-duplicate operation (line 579)

OLD:
```
  (e.g., an `automation_agent`), the reassignment step is skipped — the
```

NEW:
```
  (e.g., a `restricted_analyst`), the reassignment step is skipped — the
```

#### 2e — Confidentiality accepted risk (line 973)

OLD:
```
`automation_agent` users have `non_confidential` scope but only reach
```

NEW:
```
`restricted_analyst` users have `non_confidential` scope but only reach
```

---

### Step 3 — `docs/features/identity/user-management.md` (5 occurrences)

All five are simple string replacements of `automation_agent` →
`restricted_analyst` in CLI parameter descriptions and API filter values.

#### 3a — `manage-user create` `--role` (line 61)

OLD:
```
| `--role`       | No       | Yes        | Role to assign: `admin`, `vulnerability_analyst`, `automation_agent` |
```

NEW:
```
| `--role`       | No       | Yes        | Role to assign: `admin`, `vulnerability_analyst`, `restricted_analyst` |
```

#### 3b — `manage-user update` `--add-role` (line 148)

OLD:
```
| `--add-role`     | No       | Yes        | Role to add: `admin`, `vulnerability_analyst`, `automation_agent` |
```

NEW:
```
| `--add-role`     | No       | Yes        | Role to add: `admin`, `vulnerability_analyst`, `restricted_analyst` |
```

#### 3c — `manage-user update` `--remove-role` (line 149)

OLD:
```
| `--remove-role`  | No       | Yes        | Role to remove: `admin`, `vulnerability_analyst`, `automation_agent` |
```

NEW:
```
| `--remove-role`  | No       | Yes        | Role to remove: `admin`, `vulnerability_analyst`, `restricted_analyst` |
```

#### 3d — `manage-user list` `--role` (line 432)

OLD:
```
| `--role`     | No       | Yes        | Filter by role: `admin`, `vulnerability_analyst`, `automation_agent` |
```

NEW:
```
| `--role`     | No       | Yes        | Filter by role: `admin`, `vulnerability_analyst`, `restricted_analyst` |
```

#### 3e — List Users API `role` parameter (line 536)

OLD:
```
- `role` (enum, optional): filter by role (`admin`, `vulnerability_analyst`, `automation_agent`)
```

NEW:
```
- `role` (enum, optional): filter by role (`admin`, `vulnerability_analyst`, `restricted_analyst`)
```

---

### Step 4 — `docs/features/tickets/ticket-mutations.md` (1 occurrence)

#### 4a — Auto-assignment logic (line 646)

OLD:
```
`vulnerability_analyst` role (e.g., an `automation_agent`),
```

NEW:
```
`vulnerability_analyst` role (e.g., a `restricted_analyst`),
```

---

### Step 5 — `docs/features/identity/authentication.md` (1 occurrence)

#### 5a — Setup example (lines 931-932)

OLD:
```
   security-scanner --role automation_agent` (or `--role
```

NEW:
```
   security-scanner --role restricted_analyst` (or `--role
```

---

### Step 6 — `docs/data-model.md` (3 occurrences)

#### 6a — Role enum values table (line 909)

OLD:
```
| Automation Agent  | Automated ticket operations (same as VA except confidentiality management); scope limited to non-confidential tickets |
```

NEW:
```
| Restricted Analyst | Ticket operations with restricted scope (same capabilities as VA except confidentiality management); scope limited to non-confidential tickets |
```

#### 6b — UserRole table, role column description (line 896)

OLD:
```
| role         | ENUM        | NOT NULL                     | Role: Admin, Vulnerability Analyst, Automation Agent |
```

NEW:
```
| role         | ENUM        | NOT NULL                     | Role: Admin, Vulnerability Analyst, Restricted Analyst |
```

#### 6c — RoleMapping table, role column description (line 934)

OLD:
```
| role         | ENUM        | NOT NULL                     | Sentinel role to assign: `Admin`, `Vulnerability Analyst`, or `Automation Agent` |
```

NEW:
```
| role         | ENUM        | NOT NULL                     | Sentinel role to assign: `Admin`, `Vulnerability Analyst`, or `Restricted Analyst` |
```

---

### Step 7 — `docs/reviews/rbac.md` (3 occurrences)

#### 7a — RBAC-GAP-07 heading (line 35)

OLD:
```
### RBAC-GAP-07 — automation_agent scope vs ticket creation visibility paradox unaddressed (Medium)
```

NEW:
```
### RBAC-GAP-07 — restricted_analyst scope vs ticket creation visibility paradox unaddressed (Medium)
```

#### 7b — RBAC-GAP-07 resolution (line 37)

OLD:
```
**Status**: RESOLVED — Accepted risk: automation_agent role is only assigned to local bot accounts; internal fetchers operate at service layer without roles or scope restrictions (2026-05-26)
```

NEW:
```
**Status**: RESOLVED — Accepted risk: restricted_analyst role is assigned to accounts with restricted scope; internal fetchers operate at service layer without roles or scope restrictions (2026-05-26)
```

#### 7c — RBAC-DES-11 heading (line 115)

OLD:
```
### RBAC-DES-11 — automation_agent scope restriction bypassed by TicketAccessGrant without lifecycle control (Low)
```

NEW:
```
### RBAC-DES-11 — restricted_analyst scope restriction bypassed by TicketAccessGrant without lifecycle control (Low)
```

---

### Step 8 — `/tmp/opencode/sentinel-architecture-slides.md` (3 occurrences)

#### 8a — RBAC capability table header (line 727)

OLD:
```
| Capability | `admin` | `vulnerability_analyst` | `automation_agent` |
```

NEW:
```
| Capability | `admin` | `vulnerability_analyst` | `restricted_analyst` |
```

#### 8b — Design rule: role description (line 745)

OLD:
```
- `automation_agent` = VA minus `manage_confidentiality` (cannot see/set confidential tickets)
```

NEW:
```
- `restricted_analyst` = VA minus `manage_confidentiality` (cannot see/set confidential tickets)
```

#### 8c — Design rule: assignment (line 746)

OLD:
```
- Bots cannot be assigned as ticket owners; auto-assignment is skipped for bots
```

NEW:
```
- Non-VA users cannot be assigned as ticket owners; auto-assignment is skipped for non-VA roles
```

---

### Step 9 — Verify no stale references remain

Run a global search across the entire repository and
`/tmp/opencode/sentinel-architecture-slides.md` for all variations:

- `automation_agent`
- `Automation Agent`
- `automation agent`
- `Automation agent`

All searches must return zero results. If any result is found, update
the file before proceeding.

Also verify that no orphaned bot/AI-specific language remains near the
renamed occurrences by reviewing each modified file for terms like
"bot", "bots", "AI agent" in the surrounding context of the changes.

---

### Step 10 — Delete this draft

Remove `docs/drafts/rename-automation-agent-to-restricted-analyst.md`
from the repository.
