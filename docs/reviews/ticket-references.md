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

**Category**: Temporal/Concurrency
**Status**: OPEN

Two VAs with manage_references capability both edit the same manual reference simultaneously. Since the spec does not mention pessimistic locking for reference operations, the last write wins. Given references are supplementary metadata (not gate-affecting), this is low severity.

### TRF-GAP-11 — Empty upstream_references list behavior not explicitly documented (Low)

**Category**: Boundary Conditions
**Status**: OPEN

When a CVE has no references in its upstream data, upstream_references would be an empty list. The spec does not explicitly state whether upsert_references() handles an empty list gracefully. It should be a no-op for the CVE data references portion.

### TRF-GAP-12 — Case-insensitive path matching deviates from RFC 3986 (Low)

**Category**: Configuration/Defaults
**Status**: OPEN

The spec says case-insensitive matching for both host and path. However, URL paths are case-sensitive per RFC 3986. While this only broadens matching, the deviation is worth acknowledging explicitly.

### TRF-GAP-13 — Manual references on CVE-less tickets then CVE associated -- lifecycle path undocumented (Low)

**Category**: Data Lifecycle
**Status**: OPEN

Tickets created manually without a CVE can have manual references added. When a CVE is later associated, the fetcher's upsert strategy would correctly skip matching URLs (existing URL, manual source -> skip). This is implicitly handled but not explicitly documented as a lifecycle path.

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

**Category**: Complexity vs simplicity
**Status**: OPEN

Automatic references are system-managed and users cannot edit or delete them. If an automatic reference has a misleading auto-classified type, the VA cannot correct it. The only workaround is adding a separate manual reference with a different URL. Acceptable for v1.

### TRF-DES-05 — Unbounded reference count per ticket with no safeguard (Low)

**Category**: Scalability and maintainability
**Status**: OPEN

No upper bound enforced. High-profile CVEs could accumulate 300-500 references. The unpaginated GET returns all of them. Monitor reference counts in production before adding complexity. Response payload for 500 small objects is ~200KB -- within HTTP limits.

### TRF-DES-06 — Cross-source fill-in-NULL-only rule may preserve stale type classification (Low)

**Category**: Edge cases and risks
**Status**: OPEN

If the first source inserts a reference with an incorrect type from URL pattern matching (tier 3), a later source with correct tag-level classification (tier 2) cannot correct it because the cross-source rule prevents overwriting non-NULL values. Uncommon scenario since URL patterns are generally accurate.

### TRF-DES-07 — Source column uses free-form strings with no schema validation (Low)

**Category**: Design alternatives
**Status**: OPEN

The source column is VARCHAR(100) storing fetcher names with no FK or enum constraint. If a fetcher is renamed, existing references retain the old source name with no way to detect the discrepancy. Not worth the migration cost -- the VARCHAR approach is consistent with CVESource.source.

---

## Security

### TRF-SEC-01 — Automatic references bypass URL scheme validation (Medium)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-31)

### TRF-SEC-02 — No rate limiting on reference creation (Medium)

**Status**: RESOLVED — Accepted risk: rate limiting is a cross-cutting infrastructure concern handled by a front-end proxy if needed, not by the application layer. The per-ticket reference count is bounded operationally and monitored. (2026-05-31)

### TRF-SEC-03 — Unpaginated GET endpoint with no upper bound (Medium)

**Category**: Denial of Service
**Status**: OPEN

The GET endpoint returns all references in a single response with no pagination or hard cap. Combined with no rate limiting, this could produce arbitrarily large responses.

### TRF-SEC-04 — No audit trail for reference mutations (Medium)

**Status**: RESOLVED — Fixed: audit trail added for all manual reference mutations (reference_added, reference_deleted, reference_url_changed, reference_type_changed, reference_title_changed, reference_description_changed). The created_by field was removed; full accountability is now provided by TicketAuditEvent records. (2026-05-31)

### TRF-SEC-05 — URL validation does not check for host component validity (Low)

**Category**: Input Validation
**Status**: OPEN

The spec validates scheme and requires non-empty host but does not specify validation against private/internal network addresses (SSRF-adjacent risk). While references are not fetched server-side, they are rendered as clickable links. A URL pointing to an internal service could be used for social engineering if a VA clicks it. Low severity because URLs are not fetched by the server.

### TRF-SEC-06 — No URL content sanitization beyond scheme check (Low)

**Category**: Input Validation (XSS)
**Status**: OPEN

The spec validates URL scheme but does not mention sanitization of the URL string itself for characters that could cause issues when rendered in HTML attributes. The frontend must treat url as untrusted user input. The spec's Security section doesn't explicitly call out the frontend rendering obligation.

### TRF-SEC-07 — source field on automatic references is an unsanitized string (Low)

**Category**: Input Validation
**Status**: OPEN

The source column is populated from the fetcher name for automatic references. Fetcher names are code-defined, so low risk. However, no validation on the service layer for automatic references -- if a fetcher name exceeds 100 chars, it would cause a database error. Implementation robustness concern more than security vulnerability.

### TRF-SEC-08 — No ownership check on edit/delete -- any user with capability can modify any manual reference (Low)

**Status**: RESOLVED — Accepted risk with mitigation: any user with manage_references can modify any manual reference (intentional design for team collaboration). The new audit trail ensures full accountability — every edit/delete is recorded with actor identity in TicketAuditEvent. (2026-05-31)

### TRF-SEC-09 — No URL normalization creates bypass potential (Low)

**Category**: Input Validation
**Status**: OPEN

No normalization applied for URL comparison. The unique constraint (ticket_id, url) can be bypassed to create near-duplicate references. Not directly exploitable but could be used to clutter a ticket.

---

## API Conventions

### TRF-API-01 — Unpaginated list endpoint missing meta convention clarification (Low)

**Category**: Response envelope
**Status**: OPEN

The GET response shows {"data": [...]} without meta, which is correct per api-spec.md for unpaginated endpoints. However, the spec does not explicitly state "no meta object" to distinguish from paginated endpoints. An explicit mention would be clearer for implementers.

### TRF-API-02 — Sorting documentation could reference sort_by/sort_order convention explicitly (Low)

**Category**: Sorting
**Status**: OPEN

The spec says "Client-controlled sorting is not supported" which is conformant. However, it could be more explicit by noting that sort_by and sort_order parameters are not accepted, to make it unambiguous for implementers.

### TRF-API-03 — PATCH endpoint doesn't explicitly state empty-body rejection (Low)

**Category**: Mutation patterns
**Status**: OPEN

The PATCH endpoint states "At least one field must be provided" but does not explicitly state the 422 VALIDATION_ERROR response for empty body in the error table. The constraint is mentioned but the specific error response is not called out.
