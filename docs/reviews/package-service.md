# Review: package-service

**Spec**: `docs/features/packages/package-service.md`
**Last reviewed**: 2026-08-28
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### PKS-GAP-01 — set_track_status() does not specify WONT_FIX protection enforcement (High)

**Status**: RESOLVED — Final-status protection added to set_track_status(): system callers rejected with warning log when track is in final state (2026-05-22)

### PKS-GAP-02 — set_track_delivery_status() does not specify delivery status transition validation (High)

**Status**: RESOLVED — Delivery status transition validation added to set_track_delivery_status(); caller error handling documented in ibs-submission-tracking (2026-05-22)

### PKS-GAP-03 — set_product_status() does not distinguish VA override from propagation (High)

**Status**: RESOLVED — set_product_status() removed; product-level affectedness eliminated. Products no longer have status or is_status_override columns. (2026-05-22)

### PKS-GAP-04 — add_package_to_ticket() behavior when SMELT returns zero tracks (Medium)

**Status**: RESOLVED — Spec updated: add_package_to_ticket() now documents SMELT validation gate (zero tracks → 422, unavailable → 503) before any DB writes (2026-05-22)

### PKS-GAP-05 — Product-to-ProductRepository lookup location unspecified (Medium)

**Status**: RESOLVED — `add_package_to_ticket()` resolves target CPEs against the
current Product catalog before acquiring the Ticket lock; `add_package_records()`
receives only resolved track/product data. (2026-08-27)

### PKS-GAP-06 — Bugowner resolution and submission discovery failure behavior unspecified (Medium)

**Status**: RESOLVED — Spec updated: add_package_to_ticket() error handling now documents steps 7-8 as best-effort with explicit failure semantics (2026-05-23)

### PKS-GAP-07 — add_package_records() with empty tracks list creates orphan (Medium)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-23)

### PKS-GAP-08 — Restore pre-checks from package-model.md not reflected in module spec (Medium)

**Status**: RESOLVED — Spec updated: restore functions now include child-existence pre-checks with PACKAGE_RESTORE_BLOCKED error (2026-05-23)

### PKS-GAP-09 — TicketPackage creation in step 1 of add_package_to_ticket outside FOR UPDATE lock (Medium)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-23)

### PKS-GAP-10 — TrackData type not defined (Medium)

**Status**: RESOLVED — Replaced the undefined nominal reference with the
defined semantic `ResolvedTrackData` boundary. The specification now requires
a validated track reference, mapped `WorkflowType`, and distinct existing
local Product IDs after all external I/O, response validation, filtering,
deduplication, and CPE resolution. The concrete in-memory representation
remains an implementation choice. (2026-08-28)

### PKS-GAP-11 — Mutations on effectively-excluded records not explicitly permitted or denied (Low)

**Status**: RESOLVED — Finding invalid: soft-deleted records continue receiving all mutations per package-model.md; preconditions corrected (2026-05-25)

### PKS-GAP-12 — Delivery status regression audit event omitted (Low)

**Status**: RESOLVED — track_released event removed; delivery_status transitions never generate TicketAuditEvents (2026-05-25)

### PKS-GAP-13 — Eligibility calculation I/O location within lock not specified (Low)

**Status**: RESOLVED — Added note to add_package_records() clarifying eligibility I/O is within Transaction Hygiene Rules (single-row lookups only) (2026-06-11)

---

## Coherence

### PKS-COH-01 — set_product_status() does not specify setting is_status_override = true (High)

**Status**: RESOLVED — set_product_status() removed; no product-level is_status_override to set. Products no longer have affectedness status. (2026-05-22)

### PKS-COH-02 — set_product_eligibility() does not specify setting is_eligible_override = true (High)

**Status**: RESOLVED — Spec updated: set_product_eligibility() now documents is_eligible_override management for both override (bool) and reset (None) cases (2026-05-22)

### PKS-COH-03 — Propagation to soft-deleted products contradicts 'active only' rule (Medium)

**Status**: RESOLVED — Status propagation (track → product) removed entirely. Products no longer have affectedness status; contradiction no longer exists. (2026-05-22)

### PKS-COH-04 — set_track_status() propagation mechanism for child products unspecified vs set_product_status() (Medium)

**Status**: RESOLVED — Propagation step removed from set_track_status(); no child product status changes. Products no longer have affectedness status. (2026-05-22)

### PKS-COH-05 — IBS product release detection caller uses wrong operation (Medium)

**Status**: RESOLVED — Callers table corrected: set_product_released_at() replaces set_product_status() for IBS product release detection. (2026-05-22)

### PKS-COH-06 — track_released audit event user_id conflict between package-service and audit-log (Low)

**Status**: RESOLVED — Moot: track_released event type removed entirely from the system (2026-05-25)

### PKS-COH-07 — Note block in `set_product_eligibility()` reset path references `re_evaluate_product_eligibility` in wrong direction (Low)

**Status**: RESOLVED — Removed circular reference to `re_evaluate_product_eligibility`; note now references `resolve_eligibility_score()` directly (2026-06-11)

---

## Design

### PKS-DES-01 — Orphan chain calls reconcile_ticket_status() multiple times per operation (Medium)

**Status**: RESOLVED — Chain now calls reconcile_ticket_status() once at the end (2026-05-25)

### PKS-DES-02 — No mechanism to batch-set track statuses without repeated lock acquisition (Low)

**Status**: RESOLVED — Accepted risk: batch optimization deferred intentionally; single-call-per-track is functionally correct (2026-05-25)

### PKS-DES-03 — set_track_status() lacks WONT_FIX protection specification (High)

**Status**: RESOLVED — Cross-agent duplicate of PKS-GAP-001 (2026-05-22)

### PKS-DES-04 — set_track_delivery_status() lacks transition validation (High)

**Status**: RESOLVED — Cross-agent duplicate of PKS-GAP-002 (2026-05-22)

### PKS-DES-05 — TicketPackage creation in add_package_to_ticket() is outside the FOR UPDATE lock (Medium)

**Status**: RESOLVED — Cross-agent duplicate of PKS-GAP-009 (2026-05-22)

### PKS-DES-06 — Product status propagation mechanism unspecified (internal vs set_product_status) (High)

**Status**: RESOLVED — Cross-agent duplicate of PKS-GAP-003 (2026-05-22)

---

## Security

### PKS-SEC-01 — No authorization enforcement specified at service layer for acting_user_id (Medium)

**Status**: RESOLVED — Accepted risk: trust boundary is by design; API handler is the sole guarantor of acting_user_id correctness (2026-05-25)

### PKS-SEC-02 — Confidentiality filter delegation creates risk of bypass in new callers (Medium)

**Status**: RESOLVED — Accepted risk: confidentiality delegation to callers is by design; defensive fallback deferred (2026-05-25)

### PKS-SEC-03 — No input validation specified for package_name parameter (Low)

**Status**: RESOLVED — Accepted risk: SMELT acts as implicit validator (non-existent packages return empty results); SQL injection mitigated by SQLAlchemy; global 500-char API limit applies (2026-05-25)

### PKS-SEC-04 — search_packages ILIKE pattern not escaped (Low)

**Status**: RESOLVED — Accepted risk: no security impact; unescaped LIKE metacharacters can only produce broader search results, not data exposure (2026-05-25)

---

## API Conventions

_No findings — the spec defines service functions, not API endpoints._
