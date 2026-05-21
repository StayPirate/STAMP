# Review: package-model

**Spec**: `docs/features/packages/package-model.md`
**Last reviewed**: 2026-05-21
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

**Category**: State machine completeness
**Status**: OPEN

The spec states that `WONT_FIX` is never modified by automatic transitions, and "the automatic transition is suppressed when the current affectedness status is `WONT_FIX`." However, when delivery reaches RELEASED, the track is set to FIXED, which "triggers normal propagation to products." The spec says propagation skips products with `is_status_override = true`, but does not explicitly state whether propagation also skips products whose status is `WONT_FIX` via override. If a product inherited `WONT_FIX` from the track (before the track changed to FIXED), the `is_status_override` is false, so propagation would overwrite it to FIXED — which contradicts the "protected state" rule. The interaction between track-level protection and product-level inheritance needs clarification.

### PKM-GAP-008 — Automatic FIXED transition when delivery reaches RELEASED does not check if track status is already FIXED (Low)

**Category**: State machine completeness
**Status**: OPEN

The Automatic Transitions table states that AFFECTED or ANALYSIS transitions to FIXED when delivery reaches RELEASED. But the spec says "The VA can set FIXED manually" and "The VA can change FIXED back to AFFECTED if the fix is insufficient." If a VA manually set the track to FIXED, then changed it back to AFFECTED because the fix was insufficient, and then the same delivery event is re-processed (e.g., by reconciliation), the system would set it back to FIXED. The "one-shot" qualifier is ambiguous — it's unclear whether "one-shot" means "only triggered once per delivery event" or "only transitions forward once ever." No mechanism to prevent re-triggering is specified.

---

## Coherence

### PKM-COH-001 — Resolved gate description in package-model omits 'under a FIXED track' qualifier for product release check (Low)

**Category**: Contradictory definitions
**Status**: OPEN

In package-model.md, the Resolved gate is summarized as "all eligible products with `released_at IS NOT NULL`", without qualifying that this applies only to eligible products under FIXED tracks. The authoritative definition in tickets.md is more precise: "Every eligible product (`eligible = true`) under a `FIXED` track has `released_at IS NOT NULL`". The package-model's summary could mislead implementers into checking `released_at` on eligible products under all tracks (including NOT_AFFECTED or WONT_FIX tracks where `released_at` would be NULL).

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

**Category**: Data Exposure
**Status**: OPEN

The Security section states "Viewing affectedness data is publicly accessible (no authentication required)." Exposing which specific SUSE products are affected by which CVEs without authentication could provide attackers with actionable intelligence about unpatched systems.

### PKM-SEC-004 — No rate limiting on SMELT-triggering endpoint (Low)

**Category**: Denial of Service
**Status**: OPEN

The POST endpoint triggers an external SMELT query for every call. An authenticated VA could repeatedly call this with different non-existent package names, causing numerous outbound requests to SMELT.

---

## API Conventions

### PKM-API-001 — Inconsistent HTTP status for PACKAGE_ALREADY_EXCLUDED across endpoints (Medium)

**Status**: RESOLVED — HTTP status corrected from 422 to 409 in all soft-delete endpoints; "already in target state" is a state conflict, not a validation error (2026-05-21)

### PKM-API-002 — No read/list endpoints defined despite 'publicly accessible' viewing claim (Medium)

**Status**: RESOLVED — Two read endpoints added: `GET /api/v1/tickets/{ticket_id}/packages` (per-ticket, unpaginated) and `GET /api/v1/packages` (cross-ticket, paginated with `PackageListItem` schema, filtering, sorting). Security section updated to document access rules for both (2026-05-21)

### PKM-API-003 — PATCH endpoints with significant side-effects deviate from mutation pattern convention (Low)

**Category**: Mutation patterns
**Status**: OPEN

The "Change Track Status" and "Override Product Status" endpoints use PATCH despite triggering significant side effects. The spec acknowledges this deviation with a justification note.

### PKM-API-004 — Fixed sorting on unpaginated endpoint not explicitly stated as non-client-controlled (Low)

**Category**: Sorting conventions
**Status**: OPEN

The `GET /api/v1/tickets/{ticket_id}/packages` endpoint states "Fixed alphabetical order by `package_name`" but does not explicitly state that client-controlled sorting (`sort_by`, `sort_order` parameters) is not supported, nor does it provide a justification. The `api-spec.md` Sorting convention requires endpoints that intentionally omit client-controlled sorting to state so with justification.

### PKM-API-005 — search parameter match strategy not specified (Low)

**Category**: Query parameter conventions
**Status**: OPEN

The `GET /api/v1/packages` endpoint's `search` parameter is described as "Partial match on `package_name` (case-insensitive)" but does not specify the matching strategy (prefix match, substring match via ILIKE, or other). The service function spec in `package-service.md` clarifies this is ILIKE substring match, but the endpoint definition in `package-model.md` should state the strategy explicitly for API consumers.
