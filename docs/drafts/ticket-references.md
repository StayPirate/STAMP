# Ticket References

> **Draft status**: replaces `docs/features/tickets/ticket-references.md`
> once approved. See Migration Plan at the end of this document.

## Purpose

Provide a curated collection of external links on each ticket that helps
Vulnerability Analysts study and understand the vulnerability being
tracked. References connect tickets to external resources such as NVD
entries, vendor advisories, GitHub Security Advisories, upstream patches,
commits, pull requests, bug tracker entries, blog posts, and any other
relevant web resource.

References come from two sources:

- **Automatic**: created by CVE fetchers during ingestion. Each fetcher
  adds its own source URL (if it has a human-readable page) plus all
  references extracted from the CVE data. Automatic references are
  system-managed and cannot be edited or deleted by users.
- **Manual**: added by users with the `manage_references` capability
  through the API for any purpose (e.g., linking a Bugzilla bug, an
  upstream commit, a blog post with analysis). Manual references can be
  freely edited and deleted by any user with the `manage_references`
  capability.

All references are stored in a single `TicketReference` table associated
with the ticket, regardless of their origin. The UI presents references
grouped by type so the VA can quickly scan them and follow relevant links
for deeper analysis.

## Data Model

See `docs/data-model.md` for the full schema. Key entities:

- **ReferenceType**: content classification enum
- **TicketReference**: stores external links associated with a ticket

### ReferenceType Enum

Classifies the content that a reference URL points to.

| Value      | Description                                              |
|------------|----------------------------------------------------------|
| `advisory` | Security advisory (NVD, GHSA, vendor advisory, VDB entry) |
| `patch`    | Fix artifact: patch, commit, pull request, merge request |
| `issue`    | Bug tracker entry, issue report                          |
| `article`  | Blog post, write-up, technical analysis, mailing list post |
| `other`    | Any reference that does not fit the above categories     |

### TicketReference

| Column      | Type                       | Constraints                  | Description                        |
|-------------|----------------------------|------------------------------|------------------------------------|
| id          | UUID                       | PK                           | Internal identifier                |
| ticket_id   | UUID                       | FK(ticket.id), NOT NULL      | Related ticket                     |
| url         | VARCHAR(2048)              | NOT NULL                     | URL of the external resource       |
| title       | VARCHAR(500)               | nullable                     | Human-readable label               |
| description | VARCHAR(2000)              | nullable                     | Short note explaining relevance    |
| type        | ENUM(ReferenceType)        | NOT NULL, DEFAULT `other`    | Content classification             |
| source      | VARCHAR(100)               | NOT NULL                     | Origin: fetcher name (e.g., `"sync_cves_nvd"`) or `"manual"` for user-added references |
| created_by  | UUID                       | FK(user.id), nullable        | User who added the reference. NULL for automatic references created by fetchers |
| created_at  | TIMESTAMPTZ                | NOT NULL, DEFAULT            | Record creation timestamp          |
| updated_at  | TIMESTAMPTZ                | NOT NULL, DEFAULT            | Record update timestamp            |

**Unique constraint**: `(ticket_id, url)` — a URL cannot appear twice on
the same ticket.

**Field lengths**:
- `url`: max 2048 characters (covers virtually all real URLs)
- `title`: max 500 characters
- `description`: max 2000 characters

## Type Auto-Classification

References receive a `type` value through three mechanisms, applied in
priority order.

### Classification Priority

1. **Explicit choice** (highest): the user provides `type` in the API
   request (manual references only)
2. **CVE source tag mapping**: the fetcher maps upstream tags (e.g., NVD
   reference tags) to a `ReferenceType` value
3. **URL pattern matching**: the system infers `type` from known URL
   patterns
4. **Default** (lowest): `other`

### CVE Source Tag Mapping

When a CVE fetcher processes references from upstream data (e.g., the NVD
API v2 `references` array), each reference may carry tags from the
source. These tags are mapped to `ReferenceType` values but are **not
stored** on the `TicketReference` record — they are consumed during
classification only.

| Upstream Tag             | ReferenceType |
|--------------------------|---------------|
| `Patch`                  | `patch`       |
| `Vendor Advisory`        | `advisory`    |
| `Third Party Advisory`   | `advisory`    |
| `US Government Resource` | `advisory`    |
| `VDB Entry`              | `advisory`    |
| `Issue Tracking`         | `issue`       |
| `Exploit`                | `article`     |
| `Mailing List`           | `article`     |
| `Release Notes`          | `article`     |
| `Technical Description`  | `article`     |
| `Mitigation`             | `article`     |
| `Press/Media Coverage`   | `article`     |
| All others               | `other`       |

When a reference has multiple tags, the highest-priority type wins.
Priority order: `patch` > `advisory` > `issue` > `article` > `other`.

### URL Pattern Matching

When no upstream tag is available (or tags map to `other`), the system
attempts to infer the type from the URL. This applies to both automatic
and manual references.

| URL Pattern                              | ReferenceType |
|------------------------------------------|---------------|
| `github.com/*/commit/*`                  | `patch`       |
| `github.com/*/pull/*`                    | `patch`       |
| `gitlab.com/*/commit/*`                  | `patch`       |
| `gitlab.com/*/-/merge_requests/*`        | `patch`       |
| `git.kernel.org/*/commit/*`              | `patch`       |
| `github.com/advisories/GHSA-*`          | `advisory`    |
| `github.com/*/security/advisories/*`    | `advisory`    |
| `nvd.nist.gov/vuln/detail/*`            | `advisory`    |
| `cve.org/CVERecord*`                    | `advisory`    |
| `access.redhat.com/security/cve/*`      | `advisory`    |
| `access.redhat.com/errata/*`            | `advisory`    |
| `ubuntu.com/security/CVE-*`             | `advisory`    |
| `bugzilla.suse.com/*`                   | `issue`       |
| `bugzilla.redhat.com/*`                 | `issue`       |
| `bugs.launchpad.net/*`                  | `issue`       |

Patterns are matched case-insensitively against the URL host and path.
URLs that do not match any pattern retain the type from tag mapping, or
default to `other`.

The pattern list is maintained in code and can be extended without schema
changes.

## Fetcher Integration

### source_reference_url_pattern

CVE fetchers that inherit from `BaseFetcher` (see
`docs/features/platform/fetcher-infrastructure.md`) have an optional class
attribute `source_reference_url_pattern` that defines the URL pattern for
the fetcher's human-readable CVE page.

```python
class SyncCvesNvd(BaseFetcher):
    name = "sync_cves_nvd"
    description = "Incremental CVE sync from NVD"
    default_schedule = "0 */6 * * *"
    source_reference_url_pattern: str | None = "https://nvd.nist.gov/vuln/detail/{cve_id}"

class SyncCvesMitre(BaseFetcher):
    name = "sync_cves_mitre"
    description = "Syncs CVEs from MITRE"
    default_schedule = "0 */6 * * *"
    source_reference_url_pattern: str | None = "https://cve.org/CVERecord?id={cve_id}"
```

- When `source_reference_url_pattern` is set, the fetcher creates a
  `TicketReference` with the URL built from the pattern (replacing
  `{cve_id}` with the actual CVE ID).
- When `source_reference_url_pattern` is `None`, the fetcher does not
  create a self-referencing `TicketReference` (but still creates
  references from the CVE data if available).
- The pattern uses `{cve_id}` as the only placeholder, formatted with
  the CVE identifier (e.g., `CVE-2026-3317`).

**Convention for new CVE fetchers**: when implementing a new fetcher that
ingests CVE data, the implementer must determine whether the fetcher's
source has a human-readable web page for each CVE. If it does,
`source_reference_url_pattern` must be set with the appropriate URL
pattern so that the URL is automatically added as a `TicketReference`.

### Ingestion Flow

When a CVE fetcher processes a CVE (new or updated), the following
reference-related steps are performed **after** the ticket exists (i.e.,
after CVE upsert and ticket creation for new CVEs):

1. **Source reference** (if `source_reference_url_pattern` is set):
   upsert a `TicketReference` with:
   - `url`: pattern with `{cve_id}` replaced (e.g.,
     `https://nvd.nist.gov/vuln/detail/CVE-2026-3317`)
   - `title`: short label derived from the source name (e.g., `"NVD"`
     for `sync_cves_nvd`, `"MITRE"` for `sync_cves_mitre`)
   - `type`: `advisory` (source pages are always advisories)
   - `source`: fetcher name (e.g., `"sync_cves_nvd"`)
   - `created_by`: `NULL` (automatic)

2. **CVE data references**: for each reference URL in the CVE data (e.g.,
   the NVD API v2 `references` array), upsert a `TicketReference` with:
   - `url`: the reference URL from the CVE data
   - `title`: `NULL` (not provided by most sources)
   - `description`: `NULL`
   - `type`: auto-classified from source tags, then URL pattern, then
     `other` (see Type Auto-Classification)
   - `source`: fetcher name (e.g., `"sync_cves_nvd"`)
   - `created_by`: `NULL` (automatic)

There is no stale reference cleanup step. Fetchers only insert and update
references — they never delete them. If an upstream source removes a
reference URL, the corresponding `TicketReference` remains in Sentinel.
This is by design: references are informational links collected over
time, and removal from an upstream source does not invalidate the
information at the URL.

### Upsert Strategy

References are matched by `(ticket_id, url)` — the unique constraint.

- **New URL**: INSERT a new `TicketReference` with all fields from the
  fetcher data.
- **Existing URL, same source**: UPDATE `title`, `description`, and
  `type` if the fetcher provides new values. Since users cannot edit
  automatic references, the fetcher is the sole writer and can safely
  overwrite all fields.
- **Existing URL, different source**: no modification. The URL is already
  tracked by another fetcher or was added manually. The first source to
  insert a URL retains ownership.

Manual references (`source = "manual"`) are never modified by fetchers.

### Example

When the NVD fetcher processes CVE-2026-3317 for the first time, it
creates the following `TicketReference` records:

| url | title | type | source | created_by |
|-----|-------|------|--------|------------|
| `https://nvd.nist.gov/vuln/detail/CVE-2026-3317` | `NVD` | `advisory` | `sync_cves_nvd` | NULL |
| `https://github.com/example/project/commit/a1b2c3` | NULL | `patch` | `sync_cves_nvd` | NULL |
| `https://www.example.com/en/security-notice/vuln-2026-001` | NULL | `advisory` | `sync_cves_nvd` | NULL |

If the MITRE fetcher later processes the same CVE and finds the same
GitHub commit URL, it skips that reference (already exists with
`source = "sync_cves_nvd"`). It adds only references with new URLs.

## Mutability

### Manual references

Users with the `manage_references` capability can add, edit, and delete
manual references (`source = "manual"`) on any ticket **regardless of
ticket status**, including tickets in a final status (Resolved, Ignored).
References are supplementary metadata — adding, editing, or removing a
link does not constitute a ticket state change and is not subject to the
`require_ticket_mutable` guard.

### Automatic references

Automatic references (`source != "manual"`) are system-managed. They are
created and updated exclusively by fetchers. Users **cannot** edit or
delete automatic references through the API — the PATCH and DELETE
endpoints reject operations on automatic references with 403 Forbidden.

This separation ensures:

- **No delete+re-creation cycle**: users cannot delete an automatic
  reference that the fetcher would re-create on the next sync
- **No edit conflicts**: fetchers can safely update their references
  without overwriting user edits
- **Clear ownership**: fetchers own their references, users own theirs

Fetchers do not process soft-deleted or inactive tickets (see
`docs/features/tickets/cve-service.md`). A ticket in a final status will
not receive new automatic references through fetcher activity.

## API Endpoints

### List References

```
GET /api/v1/tickets/{ticket_id}/references
```

Returns all references for a ticket (both automatic and manual). This
endpoint is **not paginated** because the number of references per ticket
is expected to be small (typically fewer than 30). All references are
returned in a single response.

**Query parameters**:

| Parameter | Type   | Default | Description                                              |
|-----------|--------|---------|----------------------------------------------------------|
| source    | string | —       | Filter by source (e.g., `"sync_cves_nvd"`, `"manual"`)  |
| type      | string | —       | Filter by type (e.g., `"patch"`, `"advisory"`)           |

**Sorting**: results are ordered by type group priority, then by
`created_at` ascending within each group. The type group order is:

| Type       | Sort Priority |
|------------|---------------|
| `advisory` | 1             |
| `patch`    | 2             |
| `issue`    | 3             |
| `article`  | 4             |
| `other`    | 5             |

Client-controlled sorting is not supported (small dataset, defined
grouping order).

**Response** (200 OK):

```json
{
  "data": [
    {
      "id": "uuid",
      "ticket_id": "uuid",
      "url": "https://nvd.nist.gov/vuln/detail/CVE-2026-3317",
      "title": "NVD",
      "description": null,
      "type": "advisory",
      "source": "sync_cves_nvd",
      "created_by": null,
      "created_at": "2026-04-21T10:20:00Z",
      "updated_at": "2026-04-21T10:20:00Z"
    },
    {
      "id": "uuid",
      "ticket_id": "uuid",
      "url": "https://github.com/example/project/commit/a1b2c3",
      "title": null,
      "description": null,
      "type": "patch",
      "source": "sync_cves_nvd",
      "created_by": null,
      "created_at": "2026-04-21T10:20:00Z",
      "updated_at": "2026-04-21T10:20:00Z"
    },
    {
      "id": "uuid",
      "ticket_id": "uuid",
      "url": "https://bugzilla.suse.com/show_bug.cgi?id=12345",
      "title": "SUSE Bugzilla #12345",
      "description": "Upstream confirmed the fix; tracking SUSE-side packaging",
      "type": "issue",
      "source": "manual",
      "created_by": {
        "id": "uuid",
        "username": "jdoe",
        "full_name": "John Doe",
        "active": true
      },
      "created_at": "2026-04-21T14:30:00Z",
      "updated_at": "2026-04-21T14:30:00Z"
    }
  ]
}
```

**`Access: Public`**

Soft-deleted ticket protection is enforced centrally — see
`docs/api-spec.md` ([Scoped Responses](docs/api-spec.md#scoped-responses)).

**Error responses**: No endpoint-specific errors. See `docs/api-spec.md`
for global and scoped responses.

### Add Reference

```
POST /api/v1/tickets/{ticket_id}/references
```

Adds a manual reference to a ticket.

**Request body**:

```json
{
  "url": "https://bugzilla.suse.com/show_bug.cgi?id=12345",
  "title": "SUSE Bugzilla #12345",
  "description": "Upstream confirmed the fix; tracking SUSE-side packaging",
  "type": "issue"
}
```

| Field       | Type   | Required | Description                                |
|-------------|--------|----------|--------------------------------------------|
| url         | string | yes      | URL of the external resource               |
| title       | string | no       | Human-readable label                       |
| description | string | no       | Short note explaining relevance            |
| type        | string | no       | Content type (`advisory`, `patch`, `issue`, `article`, `other`). If omitted, auto-detected from URL pattern; defaults to `other` if no pattern matches |

**Response** (201 Created): the created reference object (same format as
in the list response).

**Validation rules**:
- `url` must use `https` or `http` scheme (other schemes such as
  `javascript:`, `data:`, `ftp:` are rejected)
- `url` must not exceed 2048 characters
- `url` must not already exist for this ticket (unique constraint)
- `title`, if provided, must not exceed 500 characters or be
  blank/whitespace-only
- `description`, if provided, must not exceed 2000 characters or be
  blank/whitespace-only
- `type`, if provided, must be a valid `ReferenceType` value

**Side effects**:
- `source` is always set to `"manual"`
- `created_by` is set to the authenticated user
- `type` is set to the provided value, or auto-detected from URL pattern,
  or `other`

**`Capability: manage_references`**

**Error responses**:

| Status | Code                | Condition                                     |
|--------|---------------------|-----------------------------------------------|
| 409    | `RESOURCE_CONFLICT` | URL already exists for this ticket             |
| 422    | `VALIDATION_ERROR`  | Invalid URL scheme, exceeds length limit, blank title/description, or invalid type value |

### Update Reference

```
PATCH /api/v1/tickets/{ticket_id}/references/{reference_id}
```

Updates an existing **manual** reference. Only references with
`source = "manual"` can be updated through this endpoint. Automatic
references are system-managed and cannot be modified by users.

Only the fields included in the request body are updated — omitted fields
remain unchanged.

**Request body**:

```json
{
  "title": "Updated title",
  "description": "Added context after further analysis",
  "type": "patch"
}
```

| Field       | Type   | Required | Description                     |
|-------------|--------|----------|---------------------------------|
| url         | string | no       | URL of the external resource    |
| title       | string | no       | Human-readable label            |
| description | string | no       | Short note explaining relevance |
| type        | string | no       | Content type                    |

At least one field must be provided.

**Response** (200 OK): the updated reference object (same format as in
the list response).

**`Capability: manage_references`**

**Error responses**:

| Status | Code                 | Condition                                                |
|--------|----------------------|----------------------------------------------------------|
| 403    | `FORBIDDEN`          | Reference is automatic (`source != "manual"`) — cannot be modified by users |
| 404    | `TICKET_NOT_FOUND`   | Ticket does not exist or is soft-deleted                 |
| 404    | `RESOURCE_NOT_FOUND` | Reference does not exist on this ticket                  |
| 409    | `RESOURCE_CONFLICT`  | URL already exists for this ticket (if URL was changed)  |
| 422    | `VALIDATION_ERROR`   | Invalid URL scheme, exceeds length limit, blank title/description, or invalid type value |

### Delete Reference

```
DELETE /api/v1/tickets/{ticket_id}/references/{reference_id}
```

Deletes a **manual** reference. Only references with
`source = "manual"` can be deleted through this endpoint. Automatic
references are system-managed and cannot be removed by users.

**Response** (204 No Content)

**`Capability: manage_references`**

**Error responses**:

| Status | Code                 | Condition                                        |
|--------|----------------------|--------------------------------------------------|
| 403    | `FORBIDDEN`          | Reference is automatic (`source != "manual"`) — cannot be deleted by users |
| 404    | `TICKET_NOT_FOUND`   | Ticket does not exist or is soft-deleted          |
| 404    | `RESOURCE_NOT_FOUND` | Reference does not exist on this ticket           |

## Ticket Event Logging

Reference mutations (add, edit, delete) do **not** generate
`TicketAuditEvent` records. References are supplementary external metadata
and do not constitute ticket state changes. The `TicketAuditEventType` enum
does not include reference-related event types.

## Security

- Reference list is publicly accessible (no authentication required)
- Adding, editing, and deleting references requires the `manage_references`
  capability
- Edit and delete operations are restricted to manual references
  (`source = "manual"`); automatic references cannot be modified or
  removed by users
- All manual references are editable/deletable by any user with the
  `manage_references` capability, regardless of who created them
- URL scheme is restricted to `https` and `http` to prevent injection of
  dangerous schemes (`javascript:`, `data:`, etc.)
- See `docs/features/identity/rbac.md` for the full permission model

## Boundary with CVEExternalIdentifier

`TicketReference` and `CVEExternalIdentifier` (see `docs/data-model.md`)
are related but distinct concepts:

| Aspect      | TicketReference                               | CVEExternalIdentifier                          |
|-------------|-----------------------------------------------|------------------------------------------------|
| **Purpose** | Clickable links for the VA to research the vulnerability | Identity mapping: "this CVE is also known as GHSA-xxx" |
| **Scope**   | Ticket-level (one ticket, many links)         | CVE-level (one CVE, many external IDs)         |
| **Source**   | Automatic (fetchers) + manual (VAs)           | Automatic only (fetchers, read-only)           |
| **Content** | Any relevant URL (advisories, patches, blogs) | Structured identifiers from naming authorities |

A GitHub Security Advisory (GHSA) will typically appear in both:
- `CVEExternalIdentifier`: stores the `GHSA-xxxx-xxxx-xxxx` identifier
  and its canonical URL
- `TicketReference`: stores the same URL as a clickable advisory link
  for the VA

This is not redundancy — they serve different consumers and are
maintained independently. `CVEExternalIdentifier` feeds ticket search
(searching by GHSA-ID finds the ticket). `TicketReference` feeds the
VA's research workflow.

## Dependencies

- `docs/features/tickets/cve-tracking.md` — CVE ingestion flow creates
  references. Contains the full `sync_cves_nvd` fetcher definition
  (algorithm, NVD Source API caching, metrics)
- `docs/features/platform/fetcher-infrastructure.md` — `BaseFetcher`
  contract for `source_reference_url_pattern`
- `docs/features/tickets/cve-service.md` — `UpsertResult` usage for
  post-upsert reference creation

## Cross-references

- `docs/api-spec.md` — global API conventions (envelope format, error
  codes, pagination, shared 422 responses)
- `docs/features/identity/rbac.md` — `manage_references` capability,
  endpoint permission map
- `docs/data-model.md` — `TicketReference` table definition,
  `ReferenceType` enum, `CVEExternalIdentifier` boundary

---

## Migration Plan

> Remove this section when the draft is promoted to the official
> specification.

This document replaces `docs/features/tickets/ticket-references.md`.
The following changes are required across the codebase when promoting.

### Key changes from the previous specification

| Area | Previous | New |
|------|----------|-----|
| `tags` column | ARRAY(VARCHAR), stored on TicketReference | Removed — upstream tags consumed during type classification only |
| `type` column | Not present | ENUM(ReferenceType), NOT NULL, DEFAULT `other` |
| `description` column | Not present | VARCHAR(2000), nullable |
| `url` column type | TEXT | VARCHAR(2048) |
| `title` column type | TEXT | VARCHAR(500) |
| Stale cleanup | Fetchers delete references no longer in upstream data | No cleanup — fetchers only insert and update |
| Edit/Delete scope | Any reference (automatic or manual) | Manual references only; automatic references are immutable by users |
| Type auto-classification | Not present | Three-tier: CVE tags → URL patterns → default |
| URL validation | Not specified | Scheme whitelist (https, http), max length |
| 404 error codes | Single `RESOURCE_NOT_FOUND` for both ticket and reference | Separate `TICKET_NOT_FOUND` and `RESOURCE_NOT_FOUND` |
| 403 error | Not present | `FORBIDDEN` when attempting to edit/delete automatic references |

### Step 1: Replace the existing specification

Replace `docs/features/tickets/ticket-references.md` with this document
(without the Migration Plan section).

### Step 2: Clean up review artifacts

The review findings in `docs/reviews/ticket-references.md` are for the
previous specification and no longer applicable. All 17 findings (GAP: 6
Medium + 7 Low, COH: 1 Medium + 1 Low, API: 1 Medium + 1 Low) are
superseded by the redesign.

1. **Delete `docs/reviews/ticket-references.md`**

2. **Update `docs/reviews/.tracking.json`**: remove the
   `ticket-references` entry (current lines 351-366)

3. **Update `docs/reviews/README.md`**:
   - Remove the ticket-references rows (current lines 49-50)
   - Recalculate the Total row by subtracting ticket-references findings:

     | Column | Old | Removed (TRF) | New |
     |--------|-----|---------------|-----|
     | GAP    | 16 (8M, 8L) | 13 (6M, 7L) | 3 (2M, 1L) |
     | COH    | 2 (1M, 1L)  | 2 (1M, 1L)  | 0           |
     | DES    | 1 (1L)      | 0            | 1 (1L)      |
     | SEC    | 1 (1L)      | 0            | 1 (1L)      |
     | API    | 2 (1M, 1L)  | 2 (1M, 1L)  | 0           |
     | Total  | 22          | 17           | 5           |

   The new Total severity breakdown is: 2 Medium + 3 Low

The new specification will receive fresh reviews in Step 9 (post-
promotion reviews). New findings will be tracked under a new review
file.

### Step 3: Update data-model.md

1. **Add `ReferenceType` enum** in the Enums section:
   - Values: `advisory`, `patch`, `issue`, `article`, `other`
   - PostgreSQL ENUM type

2. **Update `TicketReference` table** (current lines 1130-1149):
   - Remove `tags` column
   - Add `description` column (VARCHAR(2000), nullable)
   - Add `type` column (ENUM(ReferenceType), NOT NULL, DEFAULT `other`)
   - Change `url` type from TEXT to VARCHAR(2048)
   - Change `title` type from TEXT to VARCHAR(500)

3. **Update ERD entity** (current lines 178-184):
   - Add `type` field to the Mermaid entity block
   - Add `description` field (or keep the ERD minimal — match
     conventions of other ERD entities in the file)

4. **`source` consistency note** (current line 466): no change needed

### Step 4: Update cve-tracking.md

1. **Lines 42-44** (entity overview): mention `type`
   auto-classification instead of tags
2. **Lines 218-223** (ticket creation side effects): remove mention of
   stale cleanup, reference new upsert-only behavior and type
   classification
3. **Lines 416-421** (NVD fetcher algorithm step h): update to describe
   type classification from NVD tags, remove stale cleanup step
4. **Line 527** (Vulnrichment fetcher): update reference creation
   description
5. **Line 670** (Kernel CVE fetcher): update reference creation
   description
6. **Lines 933-934** (cross-references): no change (path is the same)

### Step 5: Update cve-service.md

1. **Lines 604-608** (UpsertResult usage): update to mention `type`
   instead of tags
2. All other mentions are generic (`TicketReference` name, ownership
   statement) and do not require changes

### Step 6: Update fetcher-infrastructure.md

1. **Lines 137-141** (source_reference_url_pattern comment): add mention
   that the created reference has `type = advisory`
2. **Lines 1129-1130** (checklist item): no substantive change needed

### Step 7: Update README files

1. **`docs/features/tickets/README.md` line 14**: update description
2. **`docs/features/README.md` line 63**: update description

### Step 8: Verify cross-reference integrity

Files that reference `ticket-references.md` or `TicketReference` but
need **no changes** (verified):

- `docs/data-sources.md` (lines 135-136, 759-760): generic
  `TicketReference` mentions, still accurate
- `docs/api-spec.md` (line 370): generic "references" sub-resource
  mention, still accurate
- `docs/features/tickets/tickets.md`: no direct references to the spec
  or model
- `docs/features/identity/rbac.md`: `manage_references` capability and
  endpoint permission map remain unchanged (same 4 endpoints, same
  paths, same access levels)

### Step 9: Post-promotion reviews

Run in parallel after all files are updated:

- `@spec-gap-analyzer` on the new `ticket-references.md`
- `@spec-coherence-reviewer` on the new `ticket-references.md`
- `@api-convention-reviewer` on the new `ticket-references.md`
- `@docs-placement-reviewer` on all modified files

Address any findings before considering the migration complete.
