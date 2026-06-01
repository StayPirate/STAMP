# Remove Ticket-Level Soft-Delete

## Purpose

Remove the ticket-level soft-delete feature from Sentinel. This document
provides the rationale, scope boundary, and a detailed execution plan for
modifying all affected specification files.

## Rationale

Ticket-level soft-delete (the ability for admins to set `deleted_at` on a
Ticket, making it invisible to all business logic and restorable later)
introduces cross-cutting complexity disproportionate to its value:

- **36 specification files** reference it; **406+ individual mentions**
- **20+ query contexts** must filter by `deleted_at IS NULL` on tickets
- **11+ background tasks** must exclude soft-deleted tickets
- Every future feature, fetcher, or query must remember to apply the filter
  (missing it is a latent bug)
- The restore operation requires `reconcile_ticket_status()` because external
  conditions may have changed, producing surprising status transitions

No use case exists that is not already covered by existing mechanisms:

| Scenario | Existing alternative |
|----------|---------------------|
| Ticket created by mistake | Mark as Ignored |
| CVE revoked by NVD | CVE rejection flow + Mark as Ignored |
| Duplicate ticket | Mark as Duplicate |
| Hide ticket from regular users | `is_confidential` flag |
| Test data in production | Operational issue, not a product feature |

Security vulnerability management platforms follow the convention of never
deleting findings — they are closed, ignored, or marked as duplicates. This
aligns with Sentinel's own audit trail philosophy.

## Scope Boundary

| Concept | Action | Justification |
|---------|--------|---------------|
| **Ticket soft-delete** (`Ticket.deleted_at`) | **REMOVE** | No unique use case; disproportionate complexity |
| **Package exclusion** (`TicketPackage.deleted_at`) | **KEEP** | VA business need: exclude irrelevant packages |
| **Track exclusion** (`TicketPackageTrack.deleted_at`) | **KEEP** | VA business need: exclude irrelevant tracks |
| **Product exclusion** (`TicketPackageProduct.deleted_at`) | **KEEP** | VA/automated business need: exclude products (EOL, irrelevant) |

The package-level exclusion model (hierarchical exclusion, orphan cleanup,
continued updates to excluded records) is unaffected by this change.

## Architectural Elements Removed

| Element | Type | Location |
|---------|------|----------|
| `Ticket.deleted_at` column | Data model | `docs/data-model.md` |
| `soft_delete_ticket()` | Service function | `ticket-service.md` |
| `restore_ticket()` | Service function | `ticket-service.md` |
| `DELETE /api/v1/tickets/{ticket_id}` | API endpoint | `tickets.md` |
| `POST /api/v1/tickets/{ticket_id}/restore` | API endpoint | `tickets.md` |
| `ticket_deleted` | Audit event type | `ticket-audit-log.md`, `data-model.md` |
| `ticket_restored` | Audit event type | `ticket-audit-log.md`, `data-model.md` |
| `TicketSoftDeletedError` | Service exception | `ticket-service.md`, `ticket-mutations.md` |
| `TicketNotDeletedError` | Service exception | `ticket-service.md` |
| `TICKET_DELETED` (HTTP 410) | Error code | `api-spec.md` |
| `TICKET_NOT_DELETED` (HTTP 409) | Error code | `api-spec.md` |
| `include_deleted` on ticket list | Query parameter | `tickets.md` |
| `include_deleted` on CVE list | Query parameter | `cve-tracking.md` |
| `include_deleted` on package search (ticket-level) | Query parameter | `package-model.md` |
| `deleted_at` in `TicketSummary` schema | Response field | `tickets.md` |
| `deleted_at` in `TicketDetail` schema | Response field | `tickets.md` |
| Step 3 (soft-delete check) in Ticket Accessibility Check | API behavior | `api-spec.md` |
| "Soft-deleted tickets" status category | Domain concept | `tickets.md`, `data-model.md` |
| Soft-delete guard in `ensure_ticket_operable()` | Guard logic | `ticket-mutations.md` |

## Definitions After Removal

### Active Ticket (simplified)

Before: `status IN (New, Analysis, Analyzed) AND deleted_at IS NULL`

After: `status IN (New, Analysis, Analyzed)`

### Status Categories (simplified)

| Category | Definition |
|----------|-----------|
| Active | Status is New, Analysis, or Analyzed |
| Inactive | Status is Resolved, Ignored, or Duplicated |

The "Soft-deleted" category is removed entirely.

### `ensure_ticket_operable()` (simplified)

Before:
1. If `ticket.deleted_at is not None` -> raise `TicketSoftDeletedError`
2. If `ticket.status` in {Ignored, Duplicated} -> raise `TicketNotMutableError`

After:
1. If `ticket.status` in {Ignored, Duplicated} -> raise `TicketNotMutableError`

The function retains its purpose (reject mutations on manually-closed tickets)
but loses the soft-delete check.

### Ticket Accessibility Check (simplified)

Before (4 steps):
1. Resolve identifier (UUID or SNTL-{n})
2. If not found -> 404
3. If `deleted_at IS NOT NULL` and caller lacks `admin_ticket_ops` -> 410
4. If confidential and caller lacks access -> 404

After (3 steps):
1. Resolve identifier (UUID or SNTL-{n})
2. If not found -> 404
3. If confidential and caller lacks access -> 404

### `admin_ticket_ops` Capability (unchanged)

The capability retains its remaining operation: "Remove CVE from ticket".
Its description is updated to reflect the reduced scope but the capability
itself is not removed or merged.

---

## Execution Plan

### Phase 1: Core Definitions

#### File: `docs/features/tickets/tickets.md`

| Action | Section / Lines | Details |
|--------|-----------------|---------|
| REMOVE | `## Soft-Delete` (~lines 673-725) | Remove the entire section |
| MODIFY | Status Categories (~lines 718-725) | Remove `AND deleted_at IS NULL` from Active definition; remove "Soft-deleted tickets" category |
| MODIFY | Mark-as-Duplicate (~lines 509-510) | Remove "soft-deleted" from `ensure_ticket_operable` parenthetical |
| REMOVE | Mark-as-Duplicate soft-deleted target (~lines 513-515) | Remove the paragraph about rejected soft-deleted targets |
| REMOVE | Mark-as-Duplicate note about 410 (~lines 535-539) | Remove the note about soft-deleted source rejection |
| REMOVE | Canonical target resolver soft-delete (~lines 610-614) | Remove paragraph about soft-deleted canonical target |
| MODIFY | Mutability Guard code block (~lines 778-784) | Remove the `deleted_at` check from the pseudocode |
| MODIFY | Mutability Guard opt-outs (~lines 787-789) | Remove `soft_delete_ticket` and `restore_ticket` from opt-out list |
| MODIFY | Mutability Guard accessibility relationship (~lines 793-799) | Remove soft-delete references |
| REMOVE | CVE Detail soft-delete sentence (~lines 917-919) | Remove "If the associated ticket is soft-deleted..." |
| REMOVE | CVE List soft-delete filtering (~lines 926-929) | Remove soft-delete filtering paragraph |
| MODIFY | Stale Access Grant Cleanup (~lines 1041-1043) | Remove soft-deleted ticket scenarios |
| MODIFY | Response schema visibility note (~lines 1087-1091) | Clarify applies only to package/track/product entities |
| REMOVE | `deleted_at` from TicketSummary schema (~line 1213) | Remove the field |
| REMOVE | `deleted_at` from TicketDetail schema (~line 1233) | Remove the field |
| REMOVE | `include_deleted` from List Tickets endpoint (~lines 1294-1299) | Remove the parameter |
| REMOVE | `POST .../restore` from endpoint-schema mapping (~line 1257) | Remove the row |
| REMOVE | `DELETE /api/v1/tickets/{ticket_id}` from mapping (~line 1260) | Remove the row |
| REMOVE | `### Soft-Delete Ticket` endpoint (~lines 1643-1660) | Remove entire section |
| REMOVE | `### Restore Ticket` endpoint (~lines 1661-1681) | Remove entire section |
| REMOVE | `[Soft-Delete](#soft-delete)` cross-reference (~line 1326) | Remove broken anchor link in Get Ticket endpoint description |
| REMOVE | `deleted_at` from Data Model summary (~line 1825) | Remove the row |
| REMOVE | Soft-delete bullet from Security section (~line 1843) | Remove the bullet |

#### File: `docs/features/tickets/ticket-service.md`

| Action | Section / Lines | Details |
|--------|-----------------|---------|
| MODIFY | Purpose (~lines 6, 15) | Remove "soft-deletion" from purpose list; simplify operability description |
| MODIFY | Operability guard (~lines 117-118) | Remove soft-delete guard entry |
| MODIFY | Operability opt-outs (~lines 122-126) | Remove `soft_delete_ticket` and `restore_ticket` bullets |
| REMOVE | `### soft_delete_ticket` (~lines 568-607) | Remove entire section |
| REMOVE | `### restore_ticket` (~lines 608-645) | Remove entire section |
| REMOVE | `TicketSoftDeletedError` exception row (~line 820) | Remove from table |
| REMOVE | `TicketNotDeletedError` exception row (~line 829) | Remove from table |
| MODIFY | Callers section (~line 846) | Update operation count |
| REMOVE | `soft_delete_ticket` from dependency summary (~line 885) | Remove row |
| REMOVE | `restore_ticket` from dependency summary (~line 886) | Remove row |
| MODIFY | `mark_as_duplicate` preconditions (~lines 470-472) | Remove "soft-deleted" from operability description |
| REMOVE | `mark_as_duplicate` `deleted_at IS NULL` filter (~line 497) | Remove the filter from cascade query — after column removal, all tickets are visible; there are no soft-deleted tickets to exclude from the cascade |
| MODIFY | `ignore_ticket` (~lines 410-411, 418-419) | Remove soft-delete references |
| MODIFY | `create_ticket` column defaults (~line 195) | Remove `deleted_at = NULL` from default column list (column no longer exists) |
| MODIFY | `grant_access` locking rationale (~line 737) | Remove "soft-delete guard" from FOR UPDATE justification; keep "immutability guard" |
| MODIFY | `revoke_access` locking rationale (~line 778) | Remove "soft-delete guard" from FOR UPDATE justification; keep "immutability guard" |

#### File: `docs/features/tickets/ticket-mutations.md`

| Action | Section / Lines | Details |
|--------|-----------------|---------|
| MODIFY | `ensure_ticket_operable()` docstring (~lines 276-286) | Remove all soft-delete references; keep mutability guard |
| REMOVE | Soft-delete guard behavior (~lines 290-291) | Remove the `deleted_at` check step |
| MODIFY | Opt-out cases (~lines 299-308) | Remove `soft_delete_ticket` and `restore_ticket` opt-outs; keep `reopen_from_ignored` and `revert_duplicate` (mutability opt-outs) |
| MODIFY | Concurrency control (~line 228) | Remove `deleted_at` from FOR UPDATE column list |
| MODIFY | Module Dependencies table (~line 87) | Remove "soft-delete/restore" from `ticket_service.py` responsibilities description |
| MODIFY | Race condition list (~line 231) | Remove "soft-delete, restore" from operations that race with gate operations |
| MODIFY | `set_cvss_assessment` operability reference (~line 380) | Remove `TicketSoftDeletedError` from `ensure_ticket_operable` call description |
| MODIFY | `reopen_from_ignored` preconditions (~lines 547-549) | Remove soft-delete guard precondition |
| MODIFY | `revert_duplicate` preconditions (~lines 591-593) | Remove soft-delete guard precondition |
| REMOVE | `TicketSoftDeletedError` exception row (~line 792) | Remove from table |
| MODIFY | Related Operations (~line 731) | Remove "soft-delete/restore" from list |

#### File: `docs/features/tickets/ticket-audit-log.md`

| Action | Section / Lines | Details |
|--------|-----------------|---------|
| REMOVE | `ticket_deleted` event type (~line 49) | Remove row from event type table |
| REMOVE | `ticket_restored` event type (~line 50) | Remove row from event type table |
| REMOVE | Soft-deleted ticket protection note (~lines 201-203) | Remove paragraph |

---

### Phase 2: Data Model

#### File: `docs/data-model.md`

| Action | Section / Lines | Details |
|--------|-----------------|---------|
| REMOVE | `deleted_at` from Ticket ER diagram (~line 152) | Remove `TIMESTAMPTZ deleted_at "nullable"` |
| REMOVE | `deleted_at` column from Ticket table (~line 1016) | Remove the row |
| MODIFY | Deletion policy (~lines 1018-1024) | Replace with: "Tickets MUST NOT be deleted from the database. There is no soft-delete mechanism at the ticket level. Tickets that are no longer relevant are transitioned to Ignored or Duplicated status." |
| MODIFY | Status categories (~lines 1055-1068) | Remove `deleted_at IS NULL` from Active definition; remove "Soft-deleted tickets" category entirely |
| MODIFY | Soft-deletion semantics note (~lines 812-819) | Remove ticket-level reference; keep package-level semantics explanation |
| REMOVE | `ticket_deleted` from TicketAuditEventType (~line 1168) | Remove row |
| REMOVE | `ticket_restored` from TicketAuditEventType (~line 1169) | Remove row |
| MODIFY | TicketAccessGrant ON DELETE RESTRICT rationale (~line 1197) | Change to "tickets are never deleted from the database; users are deactivated, not deleted" |
| MODIFY | CVEEPSSScore lifecycle note (~line 675) | Remove `deleted_at IS NULL` from active ticket definition |

---

### Phase 3: API Layer

#### File: `docs/api-spec.md`

| Action | Section / Lines | Details |
|--------|-----------------|---------|
| REMOVE | Step 3 (soft-delete check) from Ticket Accessibility Check (~lines 353-356) | Remove entirely |
| REMOVE | 410 `TICKET_DELETED` error row from accessibility table (~lines 357-361) | Remove row |
| REMOVE | `admin_ticket_ops` soft-delete bypass paragraph (~lines 362-370) | Remove entirely |
| REMOVE | Exceptions for DELETE/restore endpoints (~lines 372-382) | Remove entries |
| REMOVE | Step 2b (soft-delete) from CVE Accessibility Check (~lines 403-405) | Remove step |
| MODIFY | CVE error table condition (~line 411) | Remove "or associated ticket is soft-deleted" |
| MODIFY | Post-accessibility service-layer errors (~lines 427-434) | Remove 410 reference |
| MODIFY | Authorization Chain (~lines 56-57, 61-64) | Remove "410 for soft-deleted tickets" and soft-delete step |
| REMOVE | `include_deleted` example from Conditional Capability Checks (~lines 86-89) | Remove example |
| MODIFY | Error Code Categories (~lines 162-176) | Remove `TICKET_DELETED`, `TICKET_NOT_DELETED` from list |
| MODIFY | Manual-Zone Mutability Guard (~lines 469-471) | Remove soft-delete reference; keep only manual-zone status |
| REMOVE | CVE Accessibility rationale paragraphs (~lines 413-424) | Remove "Semantic correctness" and "Information leakage" paragraphs that explain the 404-vs-410 decision for soft-deleted tickets (decision no longer exists) |
| MODIFY | CVE Accessibility "same rules" reference (~lines 441-442) | Remove "and soft-delete rules" — keep only "the same confidentiality rules as `require_accessible_ticket`" |
| REMOVE | CVE soft-delete lifecycle note (~lines 453-455) | Remove "CVEs have no soft-delete lifecycle..." sentence (concept no longer applies) |
| MODIFY | CVE inline filtering note (~lines 462-463) | Remove "and soft-delete filtering" — keep only "confidentiality filtering is handled inline" |

#### File: `docs/features/identity/rbac.md`

| Action | Section / Lines | Details |
|--------|-----------------|---------|
| MODIFY | `admin_ticket_ops` capability definition (~line 39) | Change to: "Remove CVE from ticket" |
| REMOVE | "Soft-delete/restore tickets" from permission matrix (~line 154) | Remove row |
| REMOVE | "View deleted tickets" from permission matrix (~line 155) | Remove row |
| MODIFY | Authorization Chain (~lines 310-312) | Remove soft-delete clause and 410 reference |
| MODIFY | `ensure_ticket_operable` description (~lines 317-319) | Remove soft-delete reference |
| REMOVE | Both `include_deleted` rows from Soft Conditional Check (~lines 365-366) | Remove rows |
| REMOVE | `DELETE /api/v1/tickets/{ticket_id}` from Endpoint Permission Map (~line 433) | Remove row |
| REMOVE | `POST /api/v1/tickets/{ticket_id}/restore` from Endpoint Permission Map (~line 434) | Remove row |

---

### Phase 4: Cross-References

#### File: `docs/features/tickets/cve-tracking.md`

| Action | Section / Lines | Details |
|--------|-----------------|---------|
| REMOVE | Soft-delete pre-check subsection (~lines 317-326) | Remove entirely |
| MODIFY | Business Rule 9 (~lines 242-249) | Simplify to "Tickets MUST NOT be deleted from the database" |
| MODIFY | Router placement note (~line 80) | Remove "and soft-delete filtering" — keep only "Confidentiality filtering is handled inline via `confidential_ticket_filter()`" |
| REMOVE | Soft-delete filtering on CVE list (~lines 89-94) | Remove paragraph |
| REMOVE | `include_deleted` parameter (~line 107) | Remove from query parameters table |
| MODIFY | Re-fetch endpoint access check (~line 839) | Remove soft-delete condition |
| MODIFY | Re-fetch error 404 condition (~line 829) | Remove "or associated ticket is soft-deleted" |

#### File: `docs/features/tickets/cve-service.md`

| Action | Section / Lines | Details |
|--------|-----------------|---------|
| REMOVE | Soft-deleted ticket handling in `upsert_cve()` (~lines 203-213) | Remove paragraph — the `UNIQUE` constraint on `Ticket.cve_id` ensures no duplicate ticket is created regardless of ticket status; the paragraph's concern is fully addressed by the constraint |
| MODIFY | Active-ticket filter (~lines 264-267) | Remove `deleted_at IS NULL` and "or soft-deleted" — active ticket is now defined solely by status |
| MODIFY | Post-ingestion rejection handling (~lines 239-249) | Remove soft-delete check — rejection handling for Ignored/Duplicated tickets is already governed by `ensure_ticket_operable()` |

#### File: `docs/features/tickets/ticket-references.md`

| Action | Section / Lines | Details |
|--------|-----------------|---------|
| REMOVE | `### Ticket soft-delete` subsection (~lines 449-457) | Remove entirely |
| REMOVE | Soft-delete race condition note (~line 293) | Remove paragraph |
| REMOVE | Fetcher skip for soft-deleted tickets (~lines 410-415) | Remove paragraph |

#### File: `docs/features/tickets/cvss-scoring.md`

| Action | Section / Lines | Details |
|--------|-----------------|---------|
| REMOVE | "or `410 TICKET_DELETED`" from editability (~lines 163-164) | Remove phrase |
| MODIFY | Periodic re-fetch prose (~line 339) | Remove `deleted_at IS NULL` from active ticket definition in strategy description |
| MODIFY | Sync scope (~line 360) | Remove `deleted_at IS NULL` |
| REMOVE | 410 `TICKET_DELETED` error row from Set endpoint (~line 490) | Remove row |
| REMOVE | 410 paragraph from Set endpoint (~lines 493-497) | Remove paragraph |
| REMOVE | 410 `TICKET_DELETED` error row from Delete endpoint (~line 520) | Remove row |
| REMOVE | 410 paragraph from Delete endpoint (~lines 522-526) | Remove paragraph |
| MODIFY | Service-level flow (~line 568) | Remove "410 TICKET_DELETED if soft-deleted" |
| MODIFY | Batch recalculation scope (~line 610) | Remove `deleted_at IS NULL` |
| MODIFY | `sync_cvss_redhat` scope (~line 635) | Remove `deleted_at IS NULL` |

#### File: `docs/features/packages/package-model.md`

| Action | Section / Lines | Details |
|--------|-----------------|---------|
| MODIFY | Ticket sub-resource guard (~line 1645) | Remove "/soft-deleted" and 410 reference |
| REMOVE | 410 `TICKET_DELETED` error row (~line 1669) | Remove row |
| REMOVE | `include_deleted` ticket-level note from search (~line 1691) | Remove |
| REMOVE | `include_deleted` parameter for ticket-level (~line 1703) | Remove — confirmed this controls only ticket-level visibility (package-level exclusion is always active via `TicketPackage.deleted_at IS NULL` on line 1690) |
| REMOVE | `deleted_at` for ticket in PackageSearchItem schema (~line 1756) | Remove field |
| — | `track_summary` note (~line 1760) | **No action needed.** The `deleted_at IS NULL` at line 1760 is **track-level** (for `track_summary` counts). All ticket-level removals for this endpoint are already covered by the actions at lines 1691, 1703, and 1756 above. Do NOT touch this line |

#### File: `docs/features/packages/maintainer.md`

| Action | Section / Lines | Details |
|--------|-----------------|---------|
| REMOVE | Evaluation order step 2 (~line 98) | Remove `2. Ticket is soft-deleted → 410 TICKET_DELETED`; renumber remaining steps |
| REMOVE | Error state table row (~line 108) | Remove `Ticket is soft-deleted | 410 | —` row |
| REMOVE | Status codes table row (~line 266) | Remove `410 | TICKET_DELETED | Ticket is soft-deleted...` row |

#### File: `docs/features/packages/package-service.md`

| Action | Section / Lines | Details |
|--------|-----------------|---------|
| MODIFY | Ticket-level operability (~lines 857-868) | Remove "soft-deleted" from parenthetical |
| REMOVE | `TicketSoftDeletedError` exception row (~line 846) | Remove row |
| MODIFY | Error handling paragraph (~line 866) | Remove `TicketSoftDeletedError` |
| MODIFY | `set_product_released_at` preconditions (~lines 247-248) | Remove "soft-deleted" |
| REMOVE | `include_deleted` parameter in `search_packages` (~line 721) | Remove parameter from function signature |
| REMOVE | `caller_is_admin` parameter in `search_packages` (~line 722) | Remove parameter from function signature |
| REMOVE | Behavior step 3: soft-deleted ticket filter (~lines 734-736) | Remove entire step (step 2 — package-level `TicketPackage.deleted_at IS NULL` — is KEPT); renumber subsequent steps |

#### File: `docs/features/packages/product-lifecycle-transitions.md`

| Action | Section / Lines | Details |
|--------|-----------------|---------|
| MODIFY | Fetcher scope (~lines 57, 64) | Remove `deleted_at IS NULL` from ticket filter |

#### File: `docs/features/integrations/ibs-rabbitmq-integration.md`

| Action | Section / Lines | Details |
|--------|-----------------|---------|
| MODIFY | Monitored codestream set (~line 164) | Remove `deleted_at IS NULL` from ticket filter |
| MODIFY | Known limitations (~line 378) | Remove `deleted_at IS NULL` |

#### File: `docs/features/packages/ibs-track-release-detection.md`

| Action | Section / Lines | Details |
|--------|-----------------|---------|
| MODIFY | Active codestreams scope (~line 85) | Remove `deleted_at IS NULL` from ticket filter |
| MODIFY | Fetcher scope table (~line 205) | Remove `deleted_at IS NULL` |

#### File: `docs/features/integrations/ibs-integration.md`

| Action | Section / Lines | Details |
|--------|-----------------|---------|
| MODIFY | Business Rules item 4 (~line 244) | Clarify "soft-deleted records are not modified" to explicitly say "excluded (soft-deleted) tracks are not modified" — the current wording is ambiguous and could be misread as ticket-level soft-delete |

#### File: `docs/features/packages/ibs-submission-tracking.md`

| Action | Section / Lines | Details |
|--------|-----------------|---------|
| MODIFY | SR event processing (~line 484) | Remove `deleted_at IS NULL` from ticket filter |
| MODIFY | RequestSyncFetcher scope (~line 599) | Remove `deleted_at IS NULL` |

#### File: `docs/features/packages/package-bugowner.md`

| Action | Section / Lines | Details |
|--------|-----------------|---------|
| MODIFY | Operation 1: Prune (~line 276) | Remove `deleted_at IS NULL` from ticket filter (note: keep `TicketPackage.deleted_at IS NULL` which is package-level) |

#### File: `docs/features/platform/system-settings.md`

| Action | Section / Lines | Details |
|--------|-----------------|---------|
| MODIFY | Active ticket definition (~line 35) | Remove `deleted_at IS NULL` |

#### File: `docs/features/identity/user-service.md`

| Action | Section / Lines | Details |
|--------|-----------------|---------|
| MODIFY | `_unassign_tickets()` filter (~line 170) | Remove `deleted_at IS NULL` from query |

---

### Phase 5: Project-Level

#### File: `AGENTS.md`

| Action | Section / Lines | Details |
|--------|-----------------|---------|
| MODIFY | Guardrail 16: Centralized ticket status evaluation (~line 553) | Remove "soft-delete/restore" from the non-gate lifecycle mutations list |

#### File: `docs/features/tickets/README.md`

| Action | Section / Lines | Details |
|--------|-----------------|---------|
| MODIFY | ticket-service.md description (~lines 23-24) | Remove "soft-delete/restore" from operations list |

#### File: `docs/drafts/open-points.md`

| Action | Section / Lines | Details |
|--------|-----------------|---------|
| MODIFY | Silent-ignore open point (~lines 173-187) | Replace `include_deleted` on `GET /api/v1/tickets` example with a different capability-gated parameter (e.g., package-level `include_deleted` on `GET /api/v1/packages`) since ticket-level `include_deleted` no longer exists |

---

### Phase 6: Reviewers

After all specification changes in Phases 1-5 are applied, invoke the
following reviewers to verify coherence and correctness:

| Reviewer | Reason |
|----------|--------|
| `@spec-coherence-reviewer` | Verify no contradictions remain between specs after removing soft-delete references (run once per modified spec in `docs/features/tickets/`) |
| `@data-model-reviewer` | Verify `data-model.md` consistency after removing `deleted_at` from Ticket |
| `@api-convention-reviewer` | Verify API spec consistency after removing endpoints, error codes, and accessibility check step |
| `@docs-reviewer` | Verify documentation completeness after cross-cutting changes |
| `@docs-placement-reviewer` | Verify no orphaned rules or misplaced content after removal |
| `@spec-gap-analyzer` | Run on `tickets.md` and `ticket-service.md` to verify no gaps introduced |

Each reviewer is invoked independently on the relevant modified files.

---

### Phase 7: Cleanup

Remove this draft file:

```
rm docs/drafts/remove-ticket-soft-delete.md
```

---

## Notes

### Review Findings (Historical)

Six files in `docs/reviews/` contain resolved findings about ticket
soft-delete (e.g., `cve-service.md` CVES-GAP-13, `ticket-service.md`
TKS-GAP-02/04). These are historical artifacts documenting past gap
analyses. They are left unchanged — their context is self-contained and
they document the state of specifications at analysis time.

### `admin_ticket_ops` Capability

After removal, `admin_ticket_ops` retains one operation: "Remove CVE from
ticket". The capability is kept as-is — it may gain new administrative
operations in the future and its reduced scope does not warrant removal
or merging.

### Package-Level `include_deleted`

The `include_deleted` query parameter on package/track/product list
endpoints is unaffected. It continues to control visibility of excluded
(soft-deleted) packages, tracks, and products for users with appropriate
capabilities.

### Tickets Cannot Be Deleted

After this change, tickets cannot be deleted from the database by any
mechanism. They can only transition between statuses:
- **Ignored**: for irrelevant tickets (wrong CVE, not applicable, etc.)
- **Duplicated**: for tickets that duplicate another
- **Resolved**: for tickets where all packages have reached final status

This aligns with security platform conventions where findings are never
deleted, only closed or reclassified.
