# Draft: Eliminate Per-Endpoint Reference Lines

## Status

Draft — pending review.

## Goal

Replace the per-endpoint reference line convention with centralized
derivation tables in `api-spec.md`. This eliminates ~53 formulaic lines
scattered across 15 specification files, reduces maintenance burden, and
establishes a single source of truth for global/scoped response
applicability.

## Problem

`docs/api-spec.md` (line 377) currently requires every endpoint to
include a reference line:

> **Reference line**: each endpoint section should include a brief note
> indicating which global and scoped responses apply. Example:
> `Global responses per api-spec.md apply. Scoped: TICKET_NOT_FOUND,
> TICKET_NOT_MUTABLE.`

This convention is problematic:

1. **Redundancy**: the information is mechanically derivable from two
   data points already present in every endpoint section (access level
   and path)
2. **Unsustainable**: 29 of 83 endpoints (35%) lack the line,
   demonstrating the convention does not scale
3. **No enforcement power**: the line does not control runtime behavior
   — router dependencies do. A reference line saying "No scoped
   responses" does not prevent `require_accessible_ticket` from firing
4. **Drift risk**: if global rules change, 53+ lines need manual
   updating across 15 files

## Derivation Rules

The information conveyed by reference lines is deterministic:

**Global responses** (derived from access level):

| Access level | Applicable global responses |
|---|---|
| `Access: Public` | `422 VALIDATION_ERROR`, `500 INTERNAL_ERROR` |
| `Access: Authenticated` | `401 AUTH_NOT_AUTHENTICATED`, `403 AUTH_INSUFFICIENT_PERMISSION`, `422 VALIDATION_ERROR`, `500 INTERNAL_ERROR` |
| `Capability: <any>` | `401 AUTH_NOT_AUTHENTICATED`, `403 AUTH_INSUFFICIENT_PERMISSION`, `422 VALIDATION_ERROR`, `500 INTERNAL_ERROR` |

**Scoped responses** (derived from path and HTTP method):

| Path pattern | Scoped responses |
|---|---|
| `/api/v1/tickets/{ticket_id}/**` | `404 TICKET_NOT_FOUND` |
| `/api/v1/cves/{cve_id}/**` | `404 CVE_NOT_FOUND` |
| Mutation (POST/PATCH/DELETE) under the above routers, when the resource has an associated ticket | + `409 TICKET_NOT_MUTABLE` |
| Any other path | None |

**Known deviations**:

- **Global responses**: `POST /api/v1/auth/logout`
  (in `authentication.md`) uses custom authentication handling that
  deviates from the standard middleware. This is the only endpoint that
  deviates from global response derivation. It is annotated locally
  with an explanation of HOW and WHY it deviates.
- **Scoped responses (`TICKET_NOT_MUTABLE`)**: three POST endpoints are
  excluded from `ensure_ticket_operable()` despite being mutations under
  scoped routers:
  - `POST .../reopen` and `POST .../revert-duplicate` — manage
    manual-zone exit lifecycle (already documented in `api-spec.md`,
    Manual-Zone Mutability Guard exceptions)
  - `POST /api/v1/cves/{cve_id}/fetch` — async dispatch endpoint, does
    not mutate ticket state (requires a per-endpoint annotation — see
    Step 2b)

## Solution

1. Replace the "Reference line" paragraph in `api-spec.md` with a
   "reading contract" note that explains how to interpret endpoint
   sections (no per-endpoint reference lines needed)
2. Add a new `### Response Applicability Derivation` section in
   `api-spec.md` (between Scoped Responses and Versioning) with the
   centralized derivation tables (normative, not informative)
3. Remove all existing reference lines from feature specs
4. Preserve the genuine exception annotation (logout)

---

## Action Plan

### Step 1: Modify `docs/api-spec.md`

Two modifications in the same file, at different locations.

**Execution order**: apply Step 1b first (insertion at end of Scoped
Responses), then Step 1a (replacement at line 377). This avoids line
number shifts affecting the second edit. Alternatively, use
content-based matching (search for the exact text) rather than line
numbers.

#### Step 1a: Replace the reference line paragraph with a brief note

**File**: `docs/api-spec.md`
**Location**: lines 377-379 (inside `#### What belongs in an endpoint
error table`, which is under `### Global Responses`)

Replace this text:

```markdown
**Reference line**: each endpoint section should include a brief note
indicating which global and scoped responses apply. Example:
`Global responses per api-spec.md apply. Scoped: TICKET_NOT_FOUND, TICKET_NOT_MUTABLE.`
```

With:

```markdown
**Reading contract**: if an endpoint section has no error table, it
produces only the responses derivable from its access level and path
(see Response Applicability Derivation below). If it has an error table,
the table lists only endpoint-specific errors — global and scoped
responses are always implicit and derivable from context. Per-endpoint
reference lines are not used.
```

This replaces the obligation to add reference lines with a statement
that explains how to interpret their absence.

#### Step 1b: Add derivation section as a new `###` section

**File**: `docs/api-spec.md`
**Location**: between lines 493-494 (end of `### Scoped Responses` and
its sub-sections) and line 495 (start of `### Versioning`)

The current document structure in this region:

```
L492: See `docs/features/tickets/tickets.md` (...)
L493: for the full specification.
L494:
L495: ### Versioning
```

Insert the following new section between lines 493 and 495 (i.e., after
the blank line on 494, before `### Versioning`):

```markdown
### Response Applicability Derivation

Global and scoped responses are mechanically derivable from the
endpoint's access level and path pattern. Per-endpoint reference lines
are **not required** and MUST NOT be added to new or existing endpoints.
The derivation tables below are the single normative source of truth.

#### Global Response Derivation

| Access level | Applicable global responses |
|---|---|
| `Access: Public` | `422 VALIDATION_ERROR`, `500 INTERNAL_ERROR` |
| `Access: Authenticated` | `401 AUTH_NOT_AUTHENTICATED`, `403 AUTH_INSUFFICIENT_PERMISSION`, `422 VALIDATION_ERROR`, `500 INTERNAL_ERROR` |
| `Capability: <any>` | `401 AUTH_NOT_AUTHENTICATED`, `403 AUTH_INSUFFICIENT_PERMISSION`, `422 VALIDATION_ERROR`, `500 INTERNAL_ERROR` |

#### Scoped Response Derivation

| Path pattern | Scoped responses |
|---|---|
| `/api/v1/tickets/{ticket_id}/**` | `404 TICKET_NOT_FOUND` |
| `/api/v1/cves/{cve_id}/**` | `404 CVE_NOT_FOUND` |
| Mutation (POST/PATCH/DELETE) on ticket or CVE with associated ticket | + `409 TICKET_NOT_MUTABLE` |
| Any other path | None |

Note: `TICKET_NOT_MUTABLE` applies only to mutation endpoints
(POST/PATCH/DELETE) under the scoped routers listed above. GET endpoints
under the same routers receive only the `NOT_FOUND` scoped response.
The mechanism behind `TICKET_NOT_MUTABLE` is `ensure_ticket_operable()`
— see Manual-Zone Mutability Guard above. Endpoints excluded from
`ensure_ticket_operable()` (manual-zone exit endpoints, async dispatch
endpoints) are annotated per-endpoint and do not produce
`TICKET_NOT_MUTABLE`.

#### Genuine Exceptions

If an endpoint **deviates** from the derivation rules above (e.g., an
authenticated endpoint that does not use the standard authentication
middleware, or an endpoint under a scoped router that bypasses the
router dependency), annotate the deviation directly in the endpoint
section. The annotation must explain HOW and WHY the endpoint deviates
— it is not a formulaic reference line but a substantive explanation
of non-standard behavior.

Section-level declarations (e.g., "Global responses per api-spec.md
apply to all endpoints in this section") are not necessary and MUST NOT
be used — the derivation rules apply uniformly by access level and path.
```

**Heading level rationale**: `### Response Applicability Derivation` is
a sibling of `### Global Responses` and `### Scoped Responses`. This is
correct because the derivation covers both — it cannot be a sub-section
of either one. The `####` sub-headings inside it are consistent with
the `####` sub-headings used inside `### Scoped Responses`.

### Step 2: Remove reference lines from feature specs

For each file below, remove the specified lines. When removing a line,
also remove any resulting double-blank-lines (collapse to a single blank
line). Endpoint-specific error tables and all other content remain
unchanged.

---

#### Step 2a: `docs/features/tickets/tickets.md`

Remove 14 reference lines at these locations:

| Line | Content to remove |
|------|-------------------|
| 1262 | `Global responses per \`api-spec.md\` apply (422, 500 only — public endpoint).` |
| 1284 | `Global responses per \`api-spec.md\` apply (422, 500 only — public endpoint). Scoped: \`TICKET_NOT_FOUND\`.` |
| 1331 | `Global responses per \`api-spec.md\` apply.` |
| 1370 | `Global responses per \`api-spec.md\` apply. Scoped: \`TICKET_NOT_FOUND\`, \`TICKET_NOT_MUTABLE\`.` |
| 1405 | `Global responses per \`api-spec.md\` apply. Scoped: \`TICKET_NOT_FOUND\`, \`TICKET_NOT_MUTABLE\`.` |
| 1454 | `Global responses per \`api-spec.md\` apply. Scoped: \`TICKET_NOT_FOUND\`, \`TICKET_NOT_MUTABLE\`.` |
| 1483 | `Global responses per \`api-spec.md\` apply. Scoped: \`TICKET_NOT_FOUND\`, \`TICKET_NOT_MUTABLE\`.` |
| 1520 | `Global responses per \`api-spec.md\` apply. Scoped: \`TICKET_NOT_FOUND\`, \`TICKET_NOT_MUTABLE\`.` |
| 1554 | `Global responses per \`api-spec.md\` apply. Scoped: \`TICKET_NOT_FOUND\`.` |
| 1585 | `Global responses per \`api-spec.md\` apply. Scoped: \`TICKET_NOT_FOUND\`.` |
| 1612 | `Global responses per \`api-spec.md\` apply. Scoped: \`TICKET_NOT_FOUND\`, \`TICKET_NOT_MUTABLE\`.` |
| 1653 | `Global responses per \`api-spec.md\` apply. Scoped: \`TICKET_NOT_FOUND\`.` |
| 1683 | `Global responses per \`api-spec.md\` apply. Scoped: \`TICKET_NOT_FOUND\`, \`TICKET_NOT_MUTABLE\`.` |
| 1709 | `Global responses per \`api-spec.md\` apply. Scoped: \`TICKET_NOT_FOUND\`, \`TICKET_NOT_MUTABLE\`.` |

Each line stands alone between blank lines. Remove the line and collapse
to a single blank line separator.

---

#### Step 2b: `docs/features/tickets/cve-tracking.md`

Remove 1 reference line and add 1 deviation annotation:

| Line | Content to remove |
|------|-------------------|
| 394 | `Global responses per \`api-spec.md\` apply. Scoped: \`CVE_NOT_FOUND\`.` |

Context: sits between the response description and the `**Error
responses**:` table. Remove line + collapse blank lines.

**Add deviation annotation**: after removing the reference line, insert
the following text immediately before the `**Error responses**:` line
(separated by a blank line):

```markdown
This endpoint dispatches async fetch tasks — it is not subject to
`ensure_ticket_operable()` and does not produce `TICKET_NOT_MUTABLE`.
```

This annotation is required because the endpoint is a POST under a
scoped router (`/api/v1/cves/{cve_id}/`), and the derivation tables
would otherwise imply `TICKET_NOT_MUTABLE` applies.

---

#### Step 2c: `docs/features/tickets/cvss-scoring.md`

Remove 3 reference lines:

| Line | Content to remove |
|------|-------------------|
| 545 | `Global responses per \`api-spec.md\` apply (422, 500 only — public endpoint). Scoped: \`CVE_NOT_FOUND\`.` |
| 593 | `Global responses per \`api-spec.md\` apply. Scoped: \`CVE_NOT_FOUND\`, \`TICKET_NOT_MUTABLE\`.` |
| 622 | `Global responses per \`api-spec.md\` apply. Scoped: \`CVE_NOT_FOUND\`, \`TICKET_NOT_MUTABLE\`.` |

Each stands alone between blank lines.

---

#### Step 2d: `docs/features/tickets/ticket-audit-log.md`

Remove 1 reference line:

| Line | Content to remove |
|------|-------------------|
| 200 | `Global responses per \`api-spec.md\` apply. Scoped: \`TICKET_NOT_FOUND\`.` |

Context: between the search filter description and the
`**\`Access: Authenticated\`**` line. Remove line + collapse blank
lines.

---

#### Step 2e: `docs/features/tickets/ticket-references.md`

Remove 4 occurrences:

| Line(s) | Content to remove | Action |
|---------|-------------------|--------|
| 595-596 | `**Error responses**: No endpoint-specific errors. See \`docs/api-spec.md\`\nfor global and scoped responses.` | Remove entire 2-line block (the endpoint has no endpoint-specific errors — absence of an error table already communicates this) |
| 667 | `Global responses per \`api-spec.md\` apply. Scoped: \`TICKET_NOT_FOUND\`.` | Remove line |
| 745 | `Global responses per \`api-spec.md\` apply. Scoped: \`TICKET_NOT_FOUND\`.` | Remove line |
| 783 | `See \`docs/api-spec.md\` for global and scoped responses.` | Remove line |

Collapse resulting double-blank-lines in each case.

---

#### Step 2f: `docs/features/tickets/cve-service.md`

Remove 2 occurrences:

| Line(s) | Content to remove | Action |
|---------|-------------------|--------|
| 1284 | `Global responses per \`api-spec.md\` apply (422, 500 only — public endpoint). Scoped: \`CVE_NOT_FOUND\`.` | Remove line + collapse blank lines |
| 1408-1412 | `### Error Responses\n\nNo endpoint-specific error responses.\n\nGlobal responses per \`api-spec.md\` apply (422, 500 only — public endpoint).` | Remove entire sub-header and its content (only filler — the endpoint has no endpoint-specific errors). Ensure a single blank line remains before the next `## Exceptions` heading |

---

#### Step 2g: `docs/features/identity/authentication.md`

Remove 6 standard reference lines. **PRESERVE line 630** (genuine
exception — logout):

| Line | Content to remove |
|------|-------------------|
| 609 | `Global responses per \`api-spec.md\` apply.` |
| 673 | `Global responses per \`api-spec.md\` apply.` |
| 704 | `Global responses per \`api-spec.md\` apply.` |
| 784 | `Global responses per \`api-spec.md\` apply.` |
| 827 | `Global responses per \`api-spec.md\` apply.` |
| 910 | `Global responses per \`api-spec.md\` apply.` |

**DO NOT TOUCH line 630**:
```
Global responses per `api-spec.md` do not apply (custom authentication handling — see below).
```
This is a genuine exception annotation explaining non-standard behavior.
It stays because it documents a deviation from the derivation rules.

Each removed line stands alone between blank lines. Remove + collapse.

---

#### Step 2h: `docs/features/identity/identity-audit-log.md`

Remove 2 reference lines:

| Line | Content to remove |
|------|-------------------|
| 162 | `Global responses per \`api-spec.md\` apply.` |
| 240 | `Global responses per \`api-spec.md\` apply.` |

Each stands alone between blank lines.

---

#### Step 2i: `docs/features/identity/user-management.md`

Remove 1 reference line:

| Line | Content to remove |
|------|-------------------|
| 547 | `Global responses per \`api-spec.md\` apply (422, 500 only — public endpoint).` |

Context: between the response schema description and the
`#### Get User` heading. Remove + collapse blank lines.

---

#### Step 2j: `docs/features/identity/ad-integration.md`

Remove 1 reference line:

| Line | Content to remove |
|------|-------------------|
| 730 | `Global responses per \`api-spec.md\` apply.` |

Context: between the group CN validation rule and the `**Error
responses**:` table. Remove + collapse blank lines.

---

#### Step 2k: `docs/features/packages/package-model.md`

Remove 11 reference lines:

| Line | Content to remove |
|------|-------------------|
| 1189 | `Global responses per \`api-spec.md\` apply. Scoped: \`TICKET_NOT_FOUND\`, \`TICKET_NOT_MUTABLE\`.` |
| 1235 | `Global responses per \`api-spec.md\` apply. Scoped: \`TICKET_NOT_FOUND\`, \`TICKET_NOT_MUTABLE\`.` |
| 1272 | `Global responses per \`api-spec.md\` apply. Scoped: \`TICKET_NOT_FOUND\`, \`TICKET_NOT_MUTABLE\`.` |
| 1340 | `Global responses per \`api-spec.md\` apply. Scoped: \`TICKET_NOT_FOUND\`, \`TICKET_NOT_MUTABLE\`.` |
| 1376 | `Global responses per \`api-spec.md\` apply. Scoped: \`TICKET_NOT_FOUND\`, \`TICKET_NOT_MUTABLE\`.` |
| 1446 | `Global responses per \`api-spec.md\` apply. Scoped: \`TICKET_NOT_FOUND\`, \`TICKET_NOT_MUTABLE\`.` |
| 1480 | `Global responses per \`api-spec.md\` apply. Scoped: \`TICKET_NOT_FOUND\`, \`TICKET_NOT_MUTABLE\`.` |
| 1552 | `Global responses per \`api-spec.md\` apply. Scoped: \`TICKET_NOT_FOUND\`, \`TICKET_NOT_MUTABLE\`.` |
| 1629 | `Global responses per \`api-spec.md\` apply. Scoped: \`TICKET_NOT_FOUND\`, \`TICKET_NOT_MUTABLE\`.` |
| 1673 | `Global responses per \`api-spec.md\` apply (422, 500 only — public endpoint). Scoped: \`TICKET_NOT_FOUND\`.` |
| 1771 | `Global responses per \`api-spec.md\` apply (422, 500 only — public endpoint).` |

**Special case — line 1771**: this line is the sole content under a
`#### Error Responses` sub-header (line 1769). Remove the entire
sub-header block (lines 1769-1771):

```markdown
#### Error Responses

Global responses per `api-spec.md` apply (422, 500 only — public endpoint).
```

Ensure the `---` separator (line 1773) and subsequent content remain
correctly spaced.

All other lines (1189-1673) stand alone between blank lines.

---

#### Step 2l: `docs/features/packages/product-catalog.md`

Remove 1 reference line:

| Line | Content to remove |
|------|-------------------|
| 207 | `Global responses per \`api-spec.md\` apply (422, 500 only — public endpoint).` |

Context: between the `**\`Access: Public\`**` line and the `---`
separator. Remove + collapse.

---

#### Step 2m: `docs/features/packages/ibs-submission-tracking.md`

Remove 2 reference lines:

| Line | Content to remove |
|------|-------------------|
| 956 | `Global responses per \`api-spec.md\` apply (422, 500 only — public endpoint). Scoped: \`TICKET_NOT_FOUND\`.` |
| 1010 | `Global responses per \`api-spec.md\` apply (422, 500 only — public endpoint). Scoped: \`TICKET_NOT_FOUND\`.` |

Each stands between the response schema description and the next
heading.

---

#### Step 2n: `docs/features/platform/system-settings.md`

Remove 1 section-level declaration:

| Line | Content to remove |
|------|-------------------|
| 124 | `Global responses per \`api-spec.md\` apply to all endpoints in this section.` |

Context: between the capability declaration ("All endpoints in this
section require the `manage_settings` capability.") and the first
endpoint heading (`### Get System Settings`). Remove + collapse.

---

#### Step 2o: `docs/features/platform/fetcher-operations.md`

Remove 1 reference line:

| Line | Content to remove |
|------|-------------------|
| 676 | `Global responses per \`api-spec.md\` apply.` |

Context: between `**\`Capability: manage_fetchers\`**` and the `**Error
responses**:` table. Remove + collapse blank lines.

---

### Step 3: Verification

After all removals are complete, run the following checks:

#### Step 3a: Grep for residual reference lines

Search all files under `docs/features/` for these patterns:

- `Global responses per`
- `global and scoped responses`
- `exempt from 401/403`

**Expected results**:
- `docs/features/identity/authentication.md` line 630: the preserved
  genuine exception (`Global responses per \`api-spec.md\` do not
  apply...`) — this is correct and must remain
- `docs/features/identity/authentication.md` line ~1057: a mention in
  the Cross-references section (`docs/api-spec.md — API conventions,
  global responses, scoped responses`) — this is a cross-reference link,
  not a reference line, and is correct

Zero other matches expected.

#### Step 3b: Verify logout exception preserved

Confirm that `docs/features/identity/authentication.md` still contains
the following text in the Logout endpoint section:

```
Global responses per `api-spec.md` do not apply (custom authentication handling — see below).
```

#### Step 3c: Verify derivation table completeness

For every endpoint in the codebase, confirm that the combination of its
access level and path pattern produces a defined result in the
derivation tables. Known combinations that must work:

| Access level | Path | Expected derivation |
|---|---|---|
| `Access: Public` | `/api/v1/tickets` | 422, 500 |
| `Access: Public` | `/api/v1/tickets/{ticket_id}` | 422, 500 + `TICKET_NOT_FOUND` |
| `Access: Public` | `/api/v1/cves/{cve_id}/sources` | 422, 500 + `CVE_NOT_FOUND` |
| `Capability: create_ticket` | `/api/v1/tickets` | 401, 403, 422, 500 |
| `Capability: triage_ticket` | `/api/v1/tickets/{ticket_id}/severity` | 401, 403, 422, 500 + `TICKET_NOT_FOUND` + `TICKET_NOT_MUTABLE` |
| `Access: Authenticated` | `/api/v1/users/me` | 401, 403, 422, 500 |
| `Capability: manage_cvss` | `/api/v1/cves/{cve_id}/cvss/suse` (POST) | 401, 403, 422, 500 + `CVE_NOT_FOUND` + `TICKET_NOT_MUTABLE` |
| `Access: Public` | `/api/v1/packages` | 422, 500 |
| `Capability: manage_settings` | `/api/v1/admin/settings` | 401, 403, 422, 500 |

All must produce unambiguous results from the derivation tables. No case
should require a reference line to resolve.

### Step 4: Run reviewers

After all modifications are applied and verified:

1. **`@api-convention-reviewer`** on `docs/api-spec.md` — verify the
   new derivation convention is internally coherent, correctly placed
   within the document structure, and does not conflict with adjacent
   sections (Scoped Responses, Manual-Zone Mutability Guard)

2. **`@spec-coherence-reviewer`** on the following specs (one review
   per spec, independent sessions):
   - `docs/features/tickets/tickets.md` — highest endpoint count (14
     removals), ticket domain
   - `docs/features/identity/authentication.md` — contains the genuine
     exception, identity domain
   - `docs/features/packages/package-model.md` — second-highest count
     (11 removals), package domain

   Verify that the removal of reference lines does not create
   informational gaps or contradictions with the new convention in
   `api-spec.md`.

3. **`@docs-placement-reviewer`** on `docs/api-spec.md` — verify that
   the new derivation tables are correctly placed in `api-spec.md`
   (cross-cutting convention) rather than in individual feature specs.

### Step 5: Delete this draft

After all reviewers pass and any findings are addressed, delete
`docs/drafts/eliminate-reference-lines.md`. The derivation tables in
`api-spec.md` are now the authoritative source; this planning document
has served its purpose.

---

## Summary of Changes

| File | Removals | Special handling |
|------|----------|-----------------|
| `docs/api-spec.md` | 3 lines replaced + 1 section inserted | Reading contract replaces reference line paragraph; new `### Response Applicability Derivation` section with derivation tables |
| `docs/features/tickets/tickets.md` | 14 lines | — |
| `docs/features/tickets/cve-tracking.md` | 1 line | Add deviation annotation for fetch endpoint (not subject to `ensure_ticket_operable()`) |
| `docs/features/tickets/cvss-scoring.md` | 3 lines | — |
| `docs/features/tickets/ticket-audit-log.md` | 1 line | — |
| `docs/features/tickets/ticket-references.md` | 4 occurrences | L595-596: remove entire error-responses block |
| `docs/features/tickets/cve-service.md` | 2 occurrences | L1408-1412: remove entire `### Error Responses` sub-header |
| `docs/features/identity/authentication.md` | 6 lines | **PRESERVE L630** (genuine exception) |
| `docs/features/identity/identity-audit-log.md` | 2 lines | — |
| `docs/features/identity/user-management.md` | 1 line | — |
| `docs/features/identity/ad-integration.md` | 1 line | — |
| `docs/features/packages/package-model.md` | 11 lines | L1769-1771: remove entire `#### Error Responses` sub-header |
| `docs/features/packages/product-catalog.md` | 1 line | — |
| `docs/features/packages/ibs-submission-tracking.md` | 2 lines | — |
| `docs/features/platform/system-settings.md` | 1 line | Section-level declaration |
| `docs/features/platform/fetcher-operations.md` | 1 line | — |

**Total**: ~53 reference lines removed, 1 genuine exception preserved
(logout), 1 deviation annotation added (fetch), 1 new derivation
convention added to `api-spec.md`.
