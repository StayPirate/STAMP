# Review: package-model

**Spec**: `docs/features/packages/package-model.md`
**Last reviewed**: 2026-05-22
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### PKM-GAP-005 — No specification for querying/listing packages, tracks, and products on a ticket (High)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-21)

### PKM-GAP-001 — No endpoint to clear product status/eligibility override back to automatic (Medium)

**Status**: RESOLVED — Override reset mechanism specified via null values in PATCH endpoint; DEFAULT false added to eligible column (2026-05-21)

### PKM-GAP-002 — Behavior when soft-deleting the last active track under a package is unspecified at API level (Medium)

**Status**: RESOLVED — Cascade array added to Soft-Delete Track and Soft-Delete Product endpoint responses to signal orphan cleanup to clients (2026-05-21)

### PKM-GAP-003 — Delivery status transition from RELEASED back to IN_PROGRESS or PENDING unspecified (Medium)

**Status**: RESOLVED — Delivery status regression from IN_PROGRESS to PENDING specified (triggered by SR revocation/decline); RELEASED declared irreversible (2026-05-21)

### PKM-GAP-006 — Eligibility not recalculated when CVSS score or threshold changes for existing AFFECTED products (Medium)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-21)

### PKM-GAP-007 — Soft-deleting a product that is the last active product under a track triggers orphan cleanup but endpoint doesn't document it (Medium)

**Status**: RESOLVED — Cascade response documented by PKM-GAP-002 fix; ticket re-evaluation after soft-delete now explicitly documented in all three exclusion endpoints (2026-05-21)

### PKM-GAP-004 — WONT_FIX protected state interaction with delivery-triggered FIXED is underspecified for products (Low)

**Status**: RESOLVED — "Protected state" concept removed entirely; WONT_FIX is now treated identically to other final statuses (2026-05-22)

### PKM-GAP-008 — Automatic FIXED transition when delivery reaches RELEASED does not check if track status is already FIXED (Low)

**Status**: RESOLVED — Decoupled affectedness and delivery axes: FIXED is now triggered exclusively by track release detection (MD5 match), RELEASED exclusively by RR acceptance. One-shot coupling rule removed. (2026-05-21)

### PKM-GAP-009 — Eligibility computation for CVE-less tickets undefined (High)

**Category**: Boundary conditions
**Status**: OPEN

The eligibility rules (Axis 2) depend on the CVSS resolution cascade (`docs/features/tickets/cvss-scoring.md`) to obtain a score for threshold comparison. The cascade explicitly requires a CVE — it resolves SUSE assessments and provider scores for the ticket's CVE. However, tickets can exist without a CVE (`cve_id IS NULL`, e.g., manually created tickets). For these tickets, the entire resolution cascade has no input. The spec defines a 10.0 fallback for "no score available" but this covers the case where a CVE exists but has no assessments, not the case where no CVE exists at all. Without a defined computation path, it is ambiguous whether products on CVE-less tickets are eligible or not — this directly impacts the Resolved gate (`eligible = true` products under FIXED tracks must have `released_at IS NOT NULL`).

### PKM-GAP-010 — Reset eligibility override on CVE-less ticket unspecified (High)

**Category**: User-facing scenario gaps
**Status**: OPEN

The Override Product Eligibility endpoint specifies that sending `eligible: null` resets the override: `is_eligible_override` is set to `false` and "eligibility is immediately recalculated using the standard rules (CVSS threshold + lifecycle phase)." For CVE-less tickets, the standard rules cannot resolve a CVSS score (see PKM-GAP-009). This creates a concrete user scenario gap: a VA creates a manual ticket (no CVE), adds packages, overrides eligibility on a product, and later wants to undo the override. The recalculation path is undefined — it is unclear whether the product reverts to `eligible = true` (the database default), uses the 10.0 fallback (treating no-CVE as worst-case), or fails.

---

## Coherence

### PKM-COH-001 — Resolved gate description in package-model omits 'under a FIXED track' qualifier for product release check (Low)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-22)

---

## Design

### PKM-DES-001 — Continued updates to soft-deleted records creates unnecessary load (Medium)

**Status**: RESOLVED — Design trade-off is intentional and documented; scope clarified to active tickets only; track-level detection aligned with product-level by adding explicit active-ticket filter (2026-05-21)

### PKM-DES-002 — Hierarchical exclusion check requires multi-join on every gate evaluation (Medium)

**Status**: RESOLVED — Premature optimization; 3-table JOIN on indexed PK/FK with IS NOT NULL is trivially fast at expected cardinality; no spec change needed (2026-05-21)

### PKM-DES-003 — No mechanism to reset product overrides back to automatic inheritance (Medium)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-21)

### PKM-DES-004 — SMELT unavailability blocks manual package addition with no fallback (Medium)

**Status**: RESOLVED — Intentional design trade-off: synchronous resolution guarantees immediate consistent state; async fallback deferred as future optimization if availability issues arise (2026-05-21)

### PKM-DES-005 — Orphan cleanup after last product soft-deletion not triggered by exclude endpoint (Low)

**Status**: RESOLVED — Audit events for orphan cleanup cascade specified: each cascaded record generates its own system-triggered TicketAuditEvent; total events = 1 + len(cascade) (2026-05-21)

---

## Security

### PKM-SEC-001 — No input validation specified for package_name field (Medium)

**Status**: RESOLVED — Validation rules added: max 255 chars, alphanumeric+dots+hyphens+underscores+plus pattern, URL-encoding before SMELT query interpolation (2026-05-21)

### PKM-SEC-002 — No authorization check that package/track/product belongs to the specified ticket (Medium)

**Status**: RESOLVED — Parent-chain validation already implicit in 404 error conditions ("not found on this ticket/track" requires ownership verification) (2026-05-21)

### PKM-SEC-003 — Viewing affectedness data requires no authentication (Low)

**Status**: RESOLVED — Accepted risk: public access to affectedness data is an intentional design choice aligned with open-source transparency principles (2026-05-22)

### PKM-SEC-004 — No rate limiting on SMELT-triggering endpoint (Low)

**Status**: RESOLVED — Accepted risk: rate limiting deferred to infrastructure layer (reverse proxy); low impact given VA-only access requirement (2026-05-22)

---

## API Conventions

### PKM-API-001 — Inconsistent HTTP status for PACKAGE_ALREADY_EXCLUDED across endpoints (Medium)

**Status**: RESOLVED — HTTP status corrected from 422 to 409 in all soft-delete endpoints; "already in target state" is a state conflict, not a validation error (2026-05-21)

### PKM-API-002 — No read/list endpoints defined despite 'publicly accessible' viewing claim (Medium)

**Status**: RESOLVED — Two read endpoints added: `GET /api/v1/tickets/{ticket_id}/packages` (per-ticket, unpaginated) and `GET /api/v1/packages` (cross-ticket, paginated with `PackageListItem` schema, filtering, sorting). Security section updated to document access rules for both (2026-05-21)

### PKM-API-003 — PATCH endpoints with significant side-effects deviate from mutation pattern convention (Low)

**Status**: RESOLVED — Convention formalized in api-spec.md (Mutation Patterns section); PATCH with domain cascading consequences is now the documented pattern. Deviation notes removed from package-model.md (2026-05-22)

### PKM-API-004 — Fixed sorting on unpaginated endpoint not explicitly stated as non-client-controlled (Low)

**Status**: RESOLVED — Added explicit non-support declaration and justification for fixed sorting per api-spec.md Sorting convention (2026-05-22)

### PKM-API-005 — search parameter match strategy not specified (Low)

**Status**: RESOLVED — Clarified search parameter matching strategy as substring (ILIKE %term%) in endpoint definition (2026-05-22)
