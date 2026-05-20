# UI Content Removal from Feature Specifications

**Status**: Draft — under review (rev 5)  
**Created**: 2026-05-20  
**Last updated**: 2026-05-20  
**Scope**: Remove premature UI prescriptions from feature specs; defer UI
design until backend specifications are stable

## Motivation

The Sentinel frontend is currently a placeholder (`<h1>Sentinel</h1>`) with
no routing, components, pages, or API client. However, the project contains:

- **16 dedicated UI spec files** under `docs/features/ui/` defining page
  layouts, routing, component behavior, and interaction flows
- **~800-900 lines** of UI-prescriptive content scattered across non-UI
  feature specifications
- A premature API field (`cve_data_pending`) designed for a frontend polling
  pattern that may not be the right solution

This content was written during early project structuring, before the
backend specifications were complete. The risk is:

1. **Stale specs**: as backend specs evolve, UI specs become silently
   outdated with no implementation to validate them against
2. **Premature solutions**: UI-specific API fields and polling patterns
   lock in design choices before alternatives can be evaluated
3. **Noise**: UI prescriptions mixed into backend specs make it harder to
   review and validate the system's behavioral contracts

The decision is to remove all premature UI content, keep `docs/ui-design-system.md`
as a baseline reference, and restart UI work from scratch after backend
specifications are stable.

## Scope

### Included

- Remove `docs/features/ui/` directory (16 files — with 2 relocations
  before deletion; see "Pre-deletion Relocations" section)
- Remove `cve_data_pending` field from all specs (replace with a TBD)
- Remove pure UI prescriptions from non-UI feature specs (dialog text,
  button placement, badge colors, component specs, React-specific details,
  ASCII wireframes, chart specifications)
- Relocate business rules that are documented exclusively in UI context
- Rephrase incidental UI language in behavioral requirements
- Remove "UI Label" and "Color" columns from `data-model.md` enum tables
  (after ensuring `ui-design-system.md` covers all values)

### Excluded (kept as-is)

- `docs/ui-design-system.md` — lightweight baseline (174 lines), no risk
  of staleness, useful reference when UI work begins
- High-level behavioral requirements that inform API design (e.g., session
  behavior, SSO flow steps, severity resolution rules)
- API endpoint definitions (even in `fetcher-operations.md` — the API
  contracts are valid regardless of UI)
- CLI specifications

## Principles

Content in non-UI feature specs is categorized into three actions:

### Category A — Remove

Pure UI prescriptions: dialog text, button placement, badge colors/icons,
form field specifications, ASCII wireframes, chart axis/tooltip definitions,
React-specific implementation details (component names, polling intervals,
library recommendations), page routing, conditional component rendering.

These provide no value without a UI implementation and will be redesigned
from scratch.

### Category B — Relocate

Business rules documented exclusively in UI context. Before removing the UI
text, the underlying rule must be captured in the appropriate non-UI section
of the same spec (or a cross-cutting document).

### Category C — Rephrase

Behavioral requirements that happen to use UI language ("dropdown", "badge",
"displayed in the UI") but express valid system contracts. Remove the UI
framing; keep the business logic.

---

## Pre-deletion Relocations

Two files under `docs/features/ui/` contain authoritative API endpoint
definitions and business logic that must be relocated before the directory
is deleted.

### `docs/features/ui/references.md` → `docs/features/tickets/ticket-references.md`

This file is not a UI spec — it is the authoritative specification for the
TicketReference system. Content to relocate:

- **Data model** (lines 27-42): TicketReference table schema
- **Fetcher integration contract** (lines 44-135): `source_reference_url_pattern`
  semantics, ingestion flow, upsert strategy, stale cleanup
- **4 API endpoints** (lines 137-343): `GET/POST/PATCH/DELETE
  /api/v1/tickets/{ticket_id}/references` with full schemas
- **Business rules** (lines 384-398): ticket event logging exclusion,
  security model, editability rules

Content to discard (UI-only):

- **UI Requirements section** (lines 345-382): display formatting in ticket
  detail page
- Cross-references to `docs/features/ui/pages.md` (lines 350, 406)

After relocation, update all cross-references:

| File | Line(s) | Current reference | New reference |
|------|---------|-------------------|---------------|
| `docs/data-model.md` | 799 | `docs/features/ui/references.md` | `docs/features/tickets/ticket-references.md` |
| `tickets/cve-tracking.md` | 41, 57, 113, 127, 232 | `docs/features/ui/references.md` | `docs/features/tickets/ticket-references.md` |
| `platform/fetcher-infrastructure.md` | 65, 781 | `docs/features/ui/references.md` | `docs/features/tickets/ticket-references.md` |
| `identity/rbac.md` | 216-219 | `../ui/references.md#...` | `../tickets/ticket-references.md#...` |

### `docs/features/ui/maintainer-dashboard.md` → `docs/features/packages/maintainer.md`

This file defines the maintainer role operations. The new spec
(`maintainer.md`) takes an API-first, maintainer-centric perspective:
it defines what maintainers are, how they are identified, and what
operations the system provides for their workflow — independent of any
specific UI.

Content to relocate (with reframing as API/system spec):

- **Purpose and target audience** (lines 1-21): maintainer role definition
- **User identification** (lines 23-46): bugowner matching logic, group
  visibility rules
- **Tab filtering logic** (lines 71-168): the business rules for pending
  fixes, in-progress, and completed — these define API query criteria, not
  UI tabs. Rephrase as API endpoint filtering specifications
- **Per-ticket view business logic** (lines 172-290): evaluation order
  (ticket not found → soft-deleted → not analyzed → not bugowner → normal),
  HTTP status codes (404, 410, 200). Remove: route, navigation, page layout,
  header card, icon names, message text
- **4 API endpoints** (lines 291-504): `GET /api/v1/my/packages/{pending,
  in-progress, completed, ticket/{ticket_id}}` with full schemas
- **Security** (lines 506-517): authentication, email-based filtering
- **Performance considerations** (lines 551-571): query strategy, indexing
- **Open points** (lines 582-591): soft-deleted package filtering

Content to discard (UI-only):

- Route definitions, navigation placement (lines 52-56)
- Table column specifications, sort defaults, filter controls,
  empty state messages (lines 86-101, 123-138, 152-168, 209-248)
- UI Components section (lines 519-549): tabs/cards/badges mapping
- Visual emphasis (lines 531-536)
- Error state icon names and message text (lines 268-276) — keep only
  the evaluation order and HTTP codes

After relocation, update all cross-references:

| File | Line(s) | Current reference | New reference |
|------|---------|-------------------|---------------|
| `identity/rbac.md` | 260-263 | `../ui/maintainer-dashboard.md#...` | `../packages/maintainer.md#...` |

### `docs/ui-design-system.md` — Corrections before `data-model.md` cleanup

Before removing the "UI Label" and "Color" columns from `data-model.md`,
correct and extend `ui-design-system.md`:

1. Add `FIXED` to the Package Status Colors table (Color Intent: `success`)
2. Remove the stale `Released | success` row from the Package Status Colors
   table — `Released` is a `DeliveryStatus` value, not a `PackageStatus`
3. Add a **Delivery Status Colors** table:

   | Status      | Color Intent |
   |-------------|-------------|
   | Pending     | `muted`     |
   | In Progress | `warning`   |
   | Released    | `success`   |

After these corrections, remove the "UI Label" and "Color" columns from the
`PackageStatus` and `DeliveryStatus` enum tables in `data-model.md`
(lines 562, 574). Keep the "Type" column (final/non-final) — it is a
semantic property of the data, not a presentation concern.

---

## `cve_data_pending` — Complete Removal

### Background

`cve_data_pending` is a boolean field on the `TicketDetail` API response
schema. It was designed to support a frontend polling pattern: when a CVE
is fetched asynchronously, the field is `true`; the frontend polls until
it becomes `false`.

This is a premature solution. The underlying problem (communicating async
fetch status to consumers) has multiple potential solutions:

- Synchronous fetch (if fast enough)
- WebSocket/SSE push notification
- More granular status states (pending/fetching/failed/done)
- Not creating the ticket until CVE data is available

The specific approach should be decided during UI/UX design when the
backend is stable.

### Occurrences (7 total, 3 files)

| File | Line(s) | Content | Action |
|------|---------|---------|--------|
| `tickets/tickets.md` | 81 | `The API response includes cve_data_pending: true` | Remove phrase |
| `tickets/tickets.md` | 1429 | `cve_data_pending \| boolean \| true when CVE data is being fetched...` | Remove row from TicketDetail schema table |
| `tickets/tickets.md` | 1566-1567 | `The cve_data_pending field is true when a CVE-ID was provided...` | Remove sentence |
| `tickets/tickets.md` | 1600-1601 | `The cve_data_pending field is true when the CVE data is being fetched...` | Remove sentence |
| `tickets/cve-tracking.md` | 92-98 | Entire "Frontend behavior" paragraph (React Query, refetchInterval, polling) | Replace with TBD (see below) |
| `docs/reviews/tickets.md` | 90-92 | Gap TKT-GAP-17 (`cve_data_pending lifecycle undefined`) | Remove entry (field no longer exists) |

### Replacement TBD text (for `cve-tracking.md`)

```markdown
**TBD (UX)**: When a ticket is created with a CVE-ID that requires
async data fetching, the system needs a mechanism to communicate fetch
status to API consumers. The specific approach (API field, push
notification, synchronous fetch, or alternative) will be defined during
UI/UX design.
```

---

## Plan by File

### Tickets Domain

#### `docs/features/tickets/tickets.md`

| Action | Lines | Content |
|--------|-------|---------|
| Remove | 29 | "ticket lists, detail pages" (keep "logs, events, external communications") |
| Remove | 31-33 | "For tickets with an associated CVE, the UI shows both identifiers: `SNTL-42 (CVE-2024-1234)`. For tickets without a CVE, only `SNTL-{n}` is shown." |
| Remove | 198-199 | "The UI must provide a mechanism to create tickets manually (button placement TBD in `docs/features/ui/pages.md`)." |
| Remove | 238-244 | Entire "### UI Behavior" section (severity badge rendering rules). Business rule already documented as 400 `TICKET_SEVERITY_DERIVED` error |
| Remove | 1114-1115 | Two rows in "Tickets Without CVE" table: `CVE Information UI section \| Hidden` and `CVSS Card UI section \| Hidden` |
| Remove | 1271-1281 | Entire "### UI Requirements" section (confidential tickets badge, access grants sidebar, list rendering) |
| Rephrase | 74-75 | "to allow the frontend to link to the existing ticket" → "to identify the conflicting ticket" |
| Remove | 81, 1429, 1566-1567, 1600-1601 | All `cve_data_pending` references (see dedicated section above) |

#### `docs/features/tickets/cve-tracking.md`

| Action | Lines | Content |
|--------|-------|---------|
| Remove + TBD | 92-98 | Replace "Frontend behavior" paragraph with TBD text (see above) |
| Remove | 261-273 | Entire "## UI Requirements" section (cross-references to UI pages being removed) |
| Update | 41, 57, 113, 127, 232 | Update `docs/features/ui/references.md` → `docs/features/tickets/ticket-references.md` |

#### `docs/features/tickets/cvss-scoring.md`

| Action | Lines | Content |
|--------|-------|---------|
| Remove | 369-433 | Entire "## UI — CVSS Card" section (65 lines): tab structure, tab visibility, active tab, assessment table columns, SUSE CVSS modal. All underlying business rules are documented in API/recalculation sections |
| Rephrase | 108 | "manual input by VA via the Ticket Detail page" → "manual input by VA" |

#### `docs/features/tickets/ticket-audit-log.md`

| Action | Lines | Content |
|--------|-------|---------|
| Rephrase | 8-10 | "through a dedicated 'History' tab on the Ticket Detail page (see `docs/features/ui/pages/ticket-detail.md` for the UI specification)" → "through the audit-log API endpoint" |
| Remove | 61 | "This distinction enables the actor filter in the UI." |

---

### Identity Domain

#### `docs/features/identity/user-management.md`

| Action | Lines | Content |
|--------|-------|---------|
| Remove | 519-647 | Entire UI section content (~130 lines): Users page table/columns/badges, User detail page sections/icons/buttons, Actions for local users, Actions for AD users, Deactivation confirmation dialog (text + button colors), Reset password flow (form fields, validation, messages) |
| Keep (rephrase) | 513-517 | Preamble "## UI" establishing access levels. Rephrase as a system access-level requirement without UI framing |

**Business rules already documented elsewhere** (no relocation needed):
- Local user creation CLI-only → `ad-integration.md` Business Rule 1
- AD user password/deactivation restrictions → `user-service.md` (AD User
  Data Ownership, AD Active Status Ownership)

#### `docs/features/identity/rbac.md`

| Action | Lines | Content |
|--------|-------|---------|
| Remove | 365-377 | "### UI representation" (role origin badge display rules: "Manual" badge, AD group name badge, locked state) |
| Remove | 379-406 | "## UI Requirements" (Login page layout, User Management page, User Profile) |
| Update | 216-219 | Update Endpoint Permission Map links: `../ui/references.md#...` → `../tickets/ticket-references.md#...` |
| Update | 260-263 | Update Endpoint Permission Map links: `../ui/maintainer-dashboard.md#...` → `../packages/maintainer.md#...` |

#### `docs/features/identity/authentication.md`

| Action | Lines | Content |
|--------|-------|---------|
| Remove | 25-30 | Login page ASCII mockup portion of architecture diagram (keep the SSO/Local/Session flow diagram below it) |
| Remove | 910-931 | "## UI Surfaces" section (Personal API Keys page layout, Administration API Keys page layout: table columns, copy button, confirmation dialogs, filter controls) |
| Remove | 981-987 | Open Point about Admin API Keys page layout |
| Keep | 337-365 | "### Frontend session behavior" (post-login redirect logic, session expiration handling) — system behavioral contract |

#### `docs/features/identity/sso-authentication.md`

| Action | Lines | Content |
|--------|-------|---------|
| Remove | 473-513 | "## Login Page" section with ASCII mockups (40 lines: conditional rendering, "or" divider, form layout) |
| Rephrase | 33-36 | "The login page shows only the local credentials form — the 'Login with SUSE SSO' button is not rendered" → "SSO authentication is unavailable to the user" |
| Rephrase | 339-340 | Remove "show only the local login form (hide the SSO button)"; keep the `AUTH_SSO_DISABLED` error handling |
| Keep | 327-365 | "### Frontend flow" (steps 0-11) — essential SSO behavioral contract |

#### `docs/features/identity/local-authentication.md`

| Action | Lines | Content |
|--------|-------|---------|
| Rephrase | 314-323 | "## Login Page" section: remove button/form rendering details ("Login with SUSE SSO" button conditional, username/password form). Keep the behavioral rule: "The local login form does not check whether local users exist — it simply returns an authentication error if the credentials are invalid" (line 325-326) |

#### `docs/features/identity/ad-integration.md`

| Action | Lines | Content |
|--------|-------|---------|
| Remove | 217-219 | "yellow triangle + amber box" visual warning specification |
| Remove | 674-680 | "UI rendering notes" for preview endpoint (tooltip, info icon, conditional section hiding). Business explanation already in API field description (lines 664-666) |
| Remove | 825-857 | "## UI Requirements" section (Role Mappings page table, Add/Delete mapping flows with button specs and form fields) |

**Business rules already documented elsewhere** (no relocation needed):
- Roles applied immediately on mapping creation → Business Rule 9
  (line 897-898)
- `unknown_users` explanation ("not yet synced, will receive role at next
  sync") → API response field description (lines 664-666)

#### `docs/features/identity/identity-audit-log.md`

| Action | Lines | Content |
|--------|-------|---------|
| Remove | 248-258 | "## UI" section (audit log display contexts: admin panel vs user profile). Access-level rules already documented in the API section (actor anonymization) |

---

### Platform Domain

#### `docs/features/platform/fetcher-dashboard.md` → rename to `fetcher-operations.md`

This file has the highest concentration of UI content (~305 lines).
After removing the Frontend section, the file becomes purely an API +
CLI + access control spec. Rename to `fetcher-operations.md` to reflect
its actual content (the "dashboard" framing was UI-centric).

| Action | Lines | Content |
|--------|-------|---------|
| Remove | 695-999 | Entire "Frontend" section: Fetchers page layout, IBS RabbitMQ Consumer Card (mockups, status indicators, colored dots, tooltips), Fetcher Card component (header, dots, summary, schedule, admin controls, confirmation dialog, click target), Deregistered Fetcher Section (collapsed state, opacity, badges), Fetcher Detail Page (route, banner, admin panels), Timeline Charts (axes, colors, overlays, dash patterns, aggregate points), Time Range Selector (presets, date picker), Run History Table (columns, badges, modals), Admin Configuration Panel (form fields, toggles, inputs, help text, save button, confirmation), Dynamic Form (type-to-control mapping, label formatting algorithm, reset buttons, warning rendering), Admin Audit Log (text templates) |
| Remove | 1383-1385 | Charting library recommendation (Recharts) |
| Rename | — | `fetcher-dashboard.md` → `fetcher-operations.md` |
| Keep | 18-694 | All API endpoint definitions (IBS consumer status, list fetchers, list runs, run detail, timeline data, trigger, config GET/PATCH, audit log) |
| Keep | 1001-1015 | Access Control matrix |
| Keep | 1017-1053 | Background Tasks section |
| Keep | 1055-1367 | CLI Commands section |
| Keep | 1369-1380 | System Metrics future iteration note |

**Business rules already documented elsewhere** (no relocation needed):
- "Disabling a fetcher does not cancel a currently running execution" →
  documented in PATCH endpoint side effects (lines 603-605)
- "Deregistered fetcher" definition → documented in
  `fetcher-infrastructure.md`

**Cross-references to update after rename:**

| File | Line(s) | Action |
|------|---------|--------|
| `identity/rbac.md` | 246-254 | Update 9 Endpoint Permission Map links |
| `identity/ad-integration.md` | 547 | Update CLI section reference |
| `platform/fetcher-infrastructure.md` | 14, 471, 616 | Update cross-references |
| `docs/cli-reference.md` | 16-18 | Update 3 spec links |
| `docs/features/README.md` | 45 | Update entry |
| `docs/features/platform/README.md` | 9, 17 | Update references |
| `docs/features/integrations/ibs-rabbitmq-integration.md` | 286, 354 | Update section references |
| `docs/architecture.md` | 102 | Update reference |
| `docs/system-map.md` | 505, 584 | Update Mermaid node name and spec table link |

#### `docs/features/platform/fetcher-infrastructure.md`

| Action | Lines | Content |
|--------|-------|---------|
| Rephrase | 253 | Remove "(renders as dropdown in UI)" from `choices` property description |
| Rephrase | 254 | Remove "displayed with visual emphasis in the UI" from `warning` property description. Keep "Use for settings where incorrect values could have significant operational impact" |
| Rephrase | 301-302 | Remove "The dashboard UI (dynamic form rendering based on `settings_schema` in the GET config response)" |
| Update | 65, 781 | Update `docs/features/ui/references.md` → `docs/features/tickets/ticket-references.md` |

#### `docs/features/platform/admin.md`

| Action | Lines | Content |
|--------|-------|---------|
| Remove | 49-51 | "The Admin UI should display a confirmation dialog explaining the impact before proceeding." |
| Remove | 121-138 | "### Admin Settings Page" (route, dropdown labels, confirmation dialog text, interaction flow) |
| Remove | 222-226 | "### UI" (audit log collapsible section placement below settings form) |

**Business rules already documented elsewhere** (no relocation needed):
- Impact of changing CVSS version → documented in "Impact of changing the
  default version" section (lines 29-47)

---

### Packages Domain

#### `docs/features/packages/package-tracking.md`

| Action | Lines | Content |
|--------|-------|---------|
| Remove | 702-712 | "### UI for Soft-Deleted Records" (indicator, panel description) |
| Remove | 1532-1612 | Entire "UI Requirements" section (~80 lines): affectedness tree wireframe, status dropdown, delivery badge, popover, color coding, eligibility indicators, Add Package interaction, Excluded Items Panel, Product Release Anomaly Indicator |
| Rephrase | 95-96 | "The VA can set `FIXED` manually via the dropdown" → "The VA can set `FIXED` manually" |
| Rephrase | 308-309 | "The VA sets affectedness at the **track level** via a dropdown" → "The VA sets affectedness at the **track level**" |
| Rephrase | 617-619 | Remove "via the dropdown" from "The VA can manually change the affectedness status of any track to any value without restriction via the dropdown" |
| Rephrase | 696 | "**UI normal view** — not shown in the ticket's package tree" → "**Default views** — excluded from default ticket responses (requires `include_deleted` parameter)" |
| Remove | 925-930 | Remove the "UI confirmation" paragraph entirely (the backend permits removal of final-status packages without restriction; consequences on ticket status are expected behavior, not a special case) |

#### `docs/features/packages/package-bugowner.md`

| Action | Lines | Content |
|--------|-------|---------|
| Remove | 388-413 | "### Ticket Detail Page" section (wireframe, tooltip for group members, "Unknown" greyed-out style). The read-only rule is already in the Security section (line 425) |
| Remove | 452 | Cross-reference to `docs/features/ui/pages.md` in Cross-references section |

#### `docs/features/packages/ibs-submission-tracking.md`

| Action | Lines | Content |
|--------|-------|---------|
| Remove | 814-921 | Entire "## UI Requirements" section (~108 lines): visualization format (chain arrows, hyperlinks), color coding table, display logic, ASCII examples, progression examples, general rules |
| Relocate | 849-865 | Move "display logic" (most recent incident/SR/RR selection algorithm) to a "Chain Selection Rules" section after the API endpoints. This is a data selection rule needed by any consumer |
| Relocate | 918-920 | Move "chain relevant only for non-final track statuses (`ANALYSIS`, `AFFECTED`)" to the same Chain Selection Rules section as a filtering recommendation |
| Rephrase | 751-752 | "The data is not displayed in the UI for final-status or excluded tracks but is retained for audit and future use" → "The data is retained for final-status and excluded tracks (for audit and future use) but is not surfaced by default to consumers" |

---

### Integrations Domain

#### `docs/features/integrations/ibs-rabbitmq-integration.md`

| Action | Lines | Content |
|--------|-------|---------|
| Rephrase | 284-287 | "displayed as a dedicated card in the fetcher dashboard (see `docs/features/platform/fetcher-dashboard.md`, section 'IBS RabbitMQ Consumer Card')" → "surfaced via the `GET /api/v1/ibs-consumer/status` endpoint (see `docs/features/platform/fetcher-operations.md#ibs-rabbitmq-consumer-status`)" |
| Rephrase | 349-355 | Entire "### Dashboard Integration" subsection: remove UI framing ("card", "card grid", "positioned above"). Rephrase as: "### Operations API Integration\n\nThe consumer state is exposed via the `GET /api/v1/ibs-consumer/status` endpoint, accessible without authentication. See `docs/features/platform/fetcher-operations.md#ibs-rabbitmq-consumer-status` for the response schema." |

---

### Cross-cutting Documents

#### `docs/data-model.md`

| Action | Lines | Content |
|--------|-------|---------|
| Remove | 562 | "UI Label" and "Color" columns from PackageStatus enum table (keep "Value" and "Type" columns) |
| Remove | 574 | "UI Label" and "Color" columns from DeliveryStatus enum table (keep "Value" column) |
| Update | 799 | Update reference: `docs/features/ui/references.md` → `docs/features/tickets/ticket-references.md` |

#### `docs/features/README.md`

| Action | Lines | Content |
|--------|-------|---------|
| Remove | 14 | UI domain row from domains table |
| Remove | 57-59 | UI spec entries from spec list (`pages.md`, `maintainer-dashboard.md`, `references.md`) |
| Add | — | New entries for `tickets/ticket-references.md` and `packages/maintainer.md` in appropriate domain sections |

#### `docs/features/tickets/README.md`

| Action | Lines | Content |
|--------|-------|---------|
| Add | after line 11 | New entry: `ticket-references.md    External links on tickets (auto + manual fetcher ingestion)` |

#### `docs/features/packages/README.md`

| Action | Lines | Content |
|--------|-------|---------|
| Add | after line 18 | New entry: `maintainer.md                          Maintainer operations (pending fixes, in-progress, completed)` |
| Rephrase | 33 | "referenced by the ticket detail UI" → "referenced by the maintainer operations spec" |

#### `docs/reviews/README.md`

| Action | Lines | Content |
|--------|-------|---------|
| Remove | 51-52, 55-56, 58, 66-73, 76, 79-80 | All UI-related entries from disabled specs list: `admin-settings`, `all-tickets`, `fetcher-dashboard`, `fetcher-detail`, `fetchers`, `inbox`, `layout`, `login`, `maintainer-dashboard`, `my-packages`, `my-packages-ticket`, `my-tickets`, `orphan-tickets`, `pages`, `references`, `ticket-detail` (16 entries total) |
| Add | — | New entries: `ticket-references` (enabled, tracked under tickets domain), `maintainer` (enabled, tracked under packages domain) |
| Rename | 55 | `fetcher-dashboard` → `fetcher-operations` (if keeping as disabled; otherwise remove entirely) |

#### `docs/reviews/.tracking.json`

| Action | Keys | Content |
|--------|------|---------|
| Remove | `pages`, `references`, `maintainer-dashboard` | Remove entries for deleted/relocated specs that will no longer exist under their old names |
| Add | `ticket-references`, `maintainer` | Add new entries for the relocated specs (enabled: false initially, until first review) |
| Rename | `fetcher-dashboard` | Rename key to `fetcher-operations` (keep `enabled: false`, `cache: null`) |

#### `.opencode/agents/api-parity-reviewer.md`

| Action | Lines | Content |
|--------|-------|---------|
| Update | 34 | Remove or rephrase instruction to read `docs/features/ui/pages.md` (file will no longer exist). Replace with a general instruction to check UI-related endpoints in feature specs |

#### `.opencode/agents/fetcher-compliance-reviewer.md`

| Action | Lines | Content |
|--------|-------|---------|
| Rephrase | 18 | "integrated with the fetcher dashboard" → "integrated with the fetcher operations infrastructure" |

---

## Complete Cross-reference Inventory

All references to `docs/features/ui` across the repository (verified by
full-text search):

| File | Line(s) | Reference target | Action |
|------|---------|------------------|--------|
| `tickets/tickets.md` | 199 | `docs/features/ui/pages.md` | Remove (line being deleted) |
| `tickets/cve-tracking.md` | 41, 57, 113, 127, 232 | `docs/features/ui/references.md` | Update → `tickets/ticket-references.md` |
| `tickets/cve-tracking.md` | 264, 273 | `docs/features/ui/pages.md` | Remove (section being deleted) |
| `tickets/cvss-scoring.md` | 373 | `docs/features/ui/pages.md` | Remove (section being deleted) |
| `tickets/ticket-audit-log.md` | 10 | `docs/features/ui/pages/ticket-detail.md` | Remove, rephrase sentence |
| `packages/package-bugowner.md` | 413, 452 | `docs/features/ui/pages.md` | Remove |
| `identity/rbac.md` | 216-219 | `../ui/references.md#...` | Update → `../tickets/ticket-references.md#...` |
| `identity/rbac.md` | 260-263 | `../ui/maintainer-dashboard.md#...` | Update → `../packages/maintainer.md#...` |
| `identity/identity-audit-log.md` | 254 | `docs/features/ui/pages/admin-settings.md` | Remove (section being deleted) |
| `platform/fetcher-infrastructure.md` | 65, 781 | `docs/features/ui/references.md` | Update → `tickets/ticket-references.md` |
| `docs/data-model.md` | 799 | `docs/features/ui/references.md` | Update → `tickets/ticket-references.md` |
| `docs/features/README.md` | 14, 57-59 | `ui/` directory entries | Remove |
| `docs/reviews/README.md` | 51-52, 55-56, 58, 66-73, 76, 79-80 | disabled spec entries (16 UI-related) | Remove |
| `docs/system-map.md` | 576 | `features/ui/references.md` | Update → `features/tickets/ticket-references.md` |
| `docs/system-map.md` | 585 | `features/ui/pages.md` | Remove row |
| `docs/system-map.md` | 506, 536-543 | Mermaid: `PAGES` node + 9 edges | Remove node and all edges |
| `integrations/ibs-rabbitmq-integration.md` | 285-287 | `docs/features/platform/fetcher-dashboard.md`, section "IBS RabbitMQ Consumer Card" | Rephrase (section deleted; redirect to API endpoint) |
| `integrations/ibs-rabbitmq-integration.md` | 349-355 | "### Dashboard Integration" subsection with UI language | Rephrase as "### Operations API Integration" |
| `.opencode/agents/api-parity-reviewer.md` | 34 | `docs/features/ui/pages.md` | Update instruction |

---

## Files Not Affected

The following files were verified to contain zero UI-prescriptive content
and no `docs/features/ui/` references:

- `docs/features/identity/user-service.md`
- `docs/features/packages/ibs-track-release-detection.md`
- `docs/features/packages/ibs-product-release-detection.md` (only 2
  passing references in open items — no action needed)
- `docs/features/platform/audit-trail-infrastructure.md`
- `docs/features/integrations/ibs-integration.md`
- `docs/ui-design-system.md` (kept as baseline reference)

---

## Resolved Decisions

1. **`fetcher-dashboard.md`**: rename to `fetcher-operations.md` (reflects
   actual content: API + CLI + access control for fetcher operations)
2. **`package-tracking.md` removal warning** (lines 925-930): remove
   entirely. Removing packages in final status is not destructive — if the
   VA does it, there is a reason. Consequences on ticket status are
   expected behavior, not a special case requiring a warning
3. **Cross-references to `docs/features/ui/pages.md`**: delete without
   replacement (content will be redesigned from scratch during UI work)
4. **"Fetcher dashboard" prose terminology**: keep as-is. The term
   "fetcher dashboard" is an established concept in the codebase prose
   (8+ specs use it). The rename from `fetcher-dashboard.md` to
   `fetcher-operations.md` reflects the spec's post-cleanup content; the
   concept name "fetcher dashboard" remains valid as a colloquial label
   for the monitoring functionality. Only **file path** cross-references
   are updated — not prose mentions. Exception:
   `.opencode/agents/fetcher-compliance-reviewer.md` where the agent
   instructions should use precise spec terminology.
5. **"Maintainer Dashboard" terminology in `rbac.md`**: keep the section
   header `### Maintainer Dashboard` and prose references ("View
   maintainer dashboard") as-is. These describe a user-facing concept
   (the maintainer's work view), not a specific spec file. The Endpoint
   Permission Map links are updated to point to `../packages/maintainer.md`.
6. **`ibs-submission-tracking.md` relocated content**: place in a
   "### Chain Selection Rules" subsection (after the API endpoints
   section) rather than "Consumer Guidance" — more precise and avoids
   introducing a generic section pattern.
7. **`ui-design-system.md` Package Status Colors table**: the existing
   `Released | success` row (line 78) is a `DeliveryStatus` value
   incorrectly placed in the `PackageStatus` table. Step 1 will:
   (a) add `FIXED | success` to Package Status Colors, (b) remove the
   stale `Released` row from Package Status Colors, and (c) add the new
   DeliveryStatus Colors table.
8. **Execution atomicity**: all 11 steps are executed as a **single
   atomic commit**. Intermediate broken states (e.g., `features/README.md`
   referencing a deleted directory between Steps 5 and 10) are acceptable
   because no commit is made until all steps are complete.

---

## Execution Order

The work must be done in this order to avoid broken intermediate states.
All steps are part of a single atomic commit — no partial commits.

1. **Add missing entries to `docs/ui-design-system.md`** (add FIXED
   status, remove stale `Released` row from Package Status Colors, add
   DeliveryStatus Colors table)
2. **Create `docs/features/tickets/ticket-references.md`** from
   `references.md` (relocate non-UI content)
3. **Create `docs/features/packages/maintainer.md`** from
   `maintainer-dashboard.md` (relocate non-UI content, reframe as
   maintainer API spec)
4. **Update all cross-references** that point to the relocated files
   (includes `docs/system-map.md` line 576, domain READMEs)
5. **Remove `docs/features/ui/` directory** (all 16 files), remove the
   `pages.md` row from `docs/system-map.md` (line 585), and remove the
   `PAGES` Mermaid node + 9 edges (lines 506, 536-543)
6. **Rename `fetcher-dashboard.md` → `fetcher-operations.md`** and update
   all cross-references (9 in `rbac.md`, 3 in `fetcher-infrastructure.md`,
   3 in `cli-reference.md`, 2 in `ibs-rabbitmq-integration.md`, 1 in
   `ad-integration.md`, 1 in `architecture.md`, 2 in platform/README.md,
   1 in features/README.md, 2 in `system-map.md`)
7. **Remove `cve_data_pending`** from all specs
8. **Clean UI content from feature specs** (per-file plan above,
   including `ibs-rabbitmq-integration.md` rephrase)
9. **Remove "UI Label"/"Color" columns** from `data-model.md`
10. **Update README and tracking files**: `docs/features/README.md`,
    `docs/features/tickets/README.md`, `docs/features/packages/README.md`,
    `docs/reviews/README.md`, `docs/reviews/.tracking.json`
11. **Update agents**: `.opencode/agents/api-parity-reviewer.md`,
    `.opencode/agents/fetcher-compliance-reviewer.md`

---

## Estimated Impact

- **Files to delete**: 16 (entire `docs/features/ui/` directory)
- **Files to create**: 2 (relocated specs: `ticket-references.md`,
  `maintainer.md`)
- **Files to rename**: 1 (`fetcher-dashboard.md` → `fetcher-operations.md`)
- **Files to modify**: ~28 (17 feature specs + `data-model.md` +
  `ui-design-system.md` + `system-map.md` + `features/README.md` +
  `tickets/README.md` + `packages/README.md` + `reviews/README.md` +
  `reviews/.tracking.json` + `cli-reference.md` + `architecture.md` +
  `platform/README.md` + 2 agents + 1 review file)
- **Lines of UI content to remove**: ~800-900 total
- **Business rules to relocate first**: 3 (submission tracking chain
  selection algorithm and chain filtering rule; all small, 2-5 lines each)
- **Minor rephrases**: ~22 points (word substitutions + dashboard
  integration rephrase)
- **No implementation code affected**: `cve_data_pending` exists only in
  specs — there is no backend or frontend implementation to modify
