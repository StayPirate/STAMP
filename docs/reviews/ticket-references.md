# Review: ticket-references

**Spec**: `docs/features/tickets/ticket-references.md`
**Last reviewed**: 2026-05-30
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### TRF-GAP-01 — URL validation lacks sanitization for control characters, credentials, and injection vectors (High)

**Status**: RESOLVED — Spec updated: URL validation explicitly references RFC 3986 via Pydantic HttpUrl, which rejects control characters (2026-05-30)

### TRF-GAP-02 — Tag priority interaction with URL pattern matching can produce surprising classifications (Medium)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable; spec already defines strict priority cascade where tag mapping (priority 2) is evaluated before URL patterns (priority 3) — mutually exclusive in the cascade (2026-05-30)

### TRF-GAP-03 — No upper bound on references per ticket with unpaginated endpoint (Medium)

**Status**: RESOLVED — Spec updated: added operational limit note (≤200 references, cursor pagination as future addition) in List References section (2026-05-30)

### TRF-GAP-04 — User cannot transfer manual reference metadata when URL conflicts with automatic reference (Medium)

**Status**: RESOLVED — Accepted risk: extremely rare scenario (manual URL collision with automatic reference), current behavior is by design (2026-05-30)

### TRF-GAP-05 — Race between Phase 1 commit and upsert_references() when ticket is soft-deleted mid-flight (Medium)

**Status**: RESOLVED — Spec updated: documented soft-delete race condition as harmless by design in Transaction boundary section (2026-05-30)

### TRF-GAP-06 — Fetcher name changes would break upsert source-matching logic without data migration (Medium)

**Status**: RESOLVED — Spec updated: added source field stability note and migration requirement in Upsert Strategy section (2026-05-30)

### TRF-GAP-07 — URL change silently re-classifies type even when type was explicitly set by user (Medium)

**Status**: RESOLVED — Spec updated: PATCH without explicit type now preserves existing type; auto-classification applies only at POST creation time (2026-05-30)

### TRF-GAP-08 — Source reference URL exceeding 2048 chars -- error path unspecified (Medium)

**Status**: RESOLVED — Spec updated: added URL acceptance gate (length + control chars + scheme) with skip-and-continue strategy for all paths through upsert_references() (2026-05-30)

### TRF-GAP-09 — created_by FK ON DELETE policy not explicitly stated (Low)

**Status**: RESOLVED — Fixed: created_by column removed from TicketReference model; the FK ON DELETE concern no longer applies. (2026-05-31)

### TRF-GAP-10 — Concurrent PATCH on same manual reference -- no locking specified (Low)

**Status**: RESOLVED — Accepted risk: last-write-wins on concurrent PATCH accepted for supplementary metadata; audit trail may show stale old_value under concurrent edits, consistent with ticket-audit-log.md documentation that modules without FOR UPDATE may produce stale entries (2026-05-31)

### TRF-GAP-11 — Empty upstream_references list behavior not explicitly documented (Low)

**Status**: RESOLVED — Fixed: added explicit documentation of empty upstream_references list boundary condition in Ingestion Flow section (2026-05-31)

### TRF-GAP-12 — Case-insensitive path matching deviates from RFC 3986 (Low)

**Status**: RESOLVED — Fixed: added explicit acknowledgement of RFC 3986 §3.3 path case-sensitivity deviation with design rationale in URL Pattern Matching section (2026-05-31)

### TRF-GAP-13 — Manual references on CVE-less tickets then CVE associated -- lifecycle path undocumented (Low)

**Status**: RESOLVED — Fixed: added explicit CVE-less ticket lifecycle path documentation in CVE lifecycle events section with cross-reference to Upsert Strategy (2026-05-31)

---

## Coherence

_No issues found._

---

## Design

### TRF-DES-01 — Literal URL comparison creates near-duplicate references (Medium)

**Status**: RESOLVED — Added URL normalization rules (scheme+host lowercase, http→https, trailing slash removal) to Upsert Strategy section; restricted allowed schemes to https:// only; updated data-model.md url column description (2026-05-30)

### TRF-DES-02 — CVE association does not trigger immediate reference population (Medium)

**Status**: RESOLVED — Accepted risk: the data needed for immediate population lives in fetcher-specific extraction logic; duplicating it in the association flow is not worth the coupling for a rare scenario (CVE in DB without ticket). (2026-05-30)

### TRF-DES-03 — PATCH with URL change silently overrides user-explicit type classification (Medium)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-31)

### TRF-DES-04 — No user override path for automatic reference metadata (Low)

**Status**: RESOLVED — Accepted risk: no user override for automatic reference metadata accepted for v1; type is a presentation-only attribute with no gate impact (2026-05-31)

### TRF-DES-05 — Unbounded reference count per ticket with no safeguard (Low)

**Status**: RESOLVED — Accepted risk: unbounded reference count with monitoring-first strategy; ~200KB payload for 500 references within HTTP limits (2026-05-31)

### TRF-DES-06 — Cross-source fill-in-NULL-only rule may preserve stale type classification (Low)

**Status**: RESOLVED — Accepted risk: cross-source fill-in-NULL-only preserving stale type classification accepted; scenario uncommon with URL patterns targeting well-known domains, impact cosmetic only (2026-05-31)

### TRF-DES-07 — Source column uses free-form strings with no schema validation (Low)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes; source field stability documented at line 324 of spec, VARCHAR rationale documented in data-model.md (2026-05-31)

---

## Security

### TRF-SEC-01 — Automatic references bypass URL scheme validation (Medium)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-31)

### TRF-SEC-02 — No rate limiting on reference creation (Medium)

**Status**: RESOLVED — Accepted risk: rate limiting is a cross-cutting infrastructure concern handled by a front-end proxy if needed, not by the application layer. The per-ticket reference count is bounded operationally and monitored. (2026-05-31)

### TRF-SEC-03 — Unpaginated GET endpoint with no upper bound (Medium)

**Status**: RESOLVED — Accepted risk: unpaginated endpoint with no hard cap is a calculated risk; reference count expected to stay within operational limits; rate limiting documented as not enforced at api-spec.md level (2026-05-31)

### TRF-SEC-04 — No audit trail for reference mutations (Medium)

**Status**: RESOLVED — Fixed: audit trail added for all manual reference mutations (reference_added, reference_deleted, reference_url_changed, reference_type_changed, reference_title_changed, reference_description_changed). The created_by field was removed; full accountability is now provided by TicketAuditEvent records. (2026-05-31)

### TRF-SEC-05 — URL validation does not check for host component validity (Low)

**Status**: RESOLVED — Accepted risk: no private/internal network address filtering; URLs never fetched server-side, internal SUSE links are legitimate use case, manual references gated by manage_references capability (2026-05-31)

### TRF-SEC-06 — No URL content sanitization beyond scheme check (Low)

**Status**: RESOLVED — Accepted risk: frontend rendering security is a cross-cutting concern to be addressed in the UI repository's design system during UI implementation (2026-05-31)

### TRF-SEC-07 — source field on automatic references is an unsanitized string (Low)

**Status**: RESOLVED — Addressed in fetcher-infrastructure.md: added explicit MUST constraint on BaseFetcher.name length (max 100 chars) in Abstract Interface section (2026-05-31)

### TRF-SEC-08 — No ownership check on edit/delete -- any user with capability can modify any manual reference (Low)

**Status**: RESOLVED — Accepted risk with mitigation: any user with manage_references can modify any manual reference (intentional design for team collaboration). The new audit trail ensures full accountability — every edit/delete is recorded with actor identity in TicketAuditEvent. (2026-05-31)

### TRF-SEC-09 — No URL normalization creates bypass potential (Low)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes — URL Normalization section added to spec prevents the described bypass (2026-05-31)

---

## API Conventions

### TRF-API-01 — Unpaginated list endpoint missing meta convention clarification (Low)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable — api-spec.md already explicitly states meta is present only on paginated endpoints; duplicating in feature spec would violate Guardrail 21 (2026-05-31)

### TRF-API-02 — Sorting documentation could reference sort_by/sort_order convention explicitly (Low)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable — api-spec.md Sorting convention already defines sort_by/sort_order; repeating parameter names per-endpoint would violate Guardrail 21 (2026-05-31)

### TRF-API-03 — PATCH endpoint doesn't explicitly state empty-body rejection (Low)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable — empty body rejection is a cross-cutting convention in api-spec.md Partial Update Semantics, explicitly referenced by the spec; endpoint error tables list only endpoint-specific errors (2026-05-31)
