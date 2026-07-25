# Review: ticket-audit-log

**Spec**: `docs/features/tickets/ticket-audit-log.md`
**Last reviewed**: 2026-07-25
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### TAL-GAP-01 — Actor resolution when referenced user is deleted/deactivated (Medium)

**Status**: RESOLVED — Addressed by cross-cutting convention: added deletion/deactivation guarantees to "User References in Responses" in api-spec.md (ON DELETE RESTRICT, deactivated users resolved normally, object never null/partial) (2026-05-16)

### TAL-GAP-02 — track_status_changed vs track_released overlap (Medium)

**Status**: RESOLVED — Added track_status_changed TicketAuditEvent creation alongside track_released in Case A of ibs-track-release-detection.md (2026-05-16)

### TAL-GAP-03 — Bulk operations — event granularity unspecified (Medium)

**Status**: RESOLVED — Added granularity clause to package_added row in Event Type Contract: one event per package, child tracks/products implicit (2026-05-16)

### TAL-GAP-04 — Maximum length of old_value, new_value, and comment fields (Medium)

**Status**: RESOLVED — Changed all 8 structured comment fields from colon-separated to space-separated format, added track context to 5 event types, added "Structured comment format" rule documenting separator convention and positional parsing, updated API response example (2026-05-16)

### TAL-GAP-05 — from_date / to_date timezone handling (Medium)

**Status**: RESOLVED — Added "Timestamps & Timezones" convention to conventions.md as single source of truth; added "Date Range Interpretation" section to api-spec.md with timezone parsing rules (naive=UTC, offset=convert to UTC); updated data-model.md and all feature specs from TIMESTAMP to TIMESTAMPTZ (2026-05-16)

### TAL-GAP-06 — search filter on NULL comment field (Low)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-17)

### TAL-GAP-07 — Concurrent mutations producing conflicting audit events (Low)

**Status**: RESOLVED — Addressed: pessimistic locking (SELECT FOR UPDATE) added to ticket_mutations module specification and conventions (2026-05-17)

---

## Coherence

_No findings — clean review._

---

## Design

### TAL-DES-01 — Overloaded comment field as structured data carrier (Medium)

**Status**: RESOLVED — Structured data migrated from comment to detail JSONB column with validated schema contract, aligning with IdentityAuditEvent pattern (2026-05-16)

### TAL-DES-02 — track_released vs track_status_changed overlap (Low)

**Status**: RESOLVED — Cross-agent duplicate of TAL-GAP-02 (2026-05-16)

### TAL-DES-03 — Unbounded ILIKE search on comment (Medium)

**Status**: RESOLVED — Accepted risk: per-ticket scoping provides sufficient mitigation; pg_trgm index can be added operationally if needed (2026-05-17)

### TAL-DES-04 — Indefinite retention with no archival path (Low)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-17)

---

## Security

### TAL-SEC-01 — Public access to full audit history (Medium)

**Status**: RESOLVED — Access level changed from Public to Authenticated; endpoint now requires authentication; History tab hidden for non-authenticated UI visitors (2026-05-17)

### TAL-SEC-02 — ILIKE search without length limit (Low)

**Status**: RESOLVED — Added global query parameter length limit (500 chars) in api-spec.md and implementation guidance in conventions.md (2026-05-17)

### TAL-SEC-03 — No rate limiting specified (Low)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-17)

---

## API Conventions

### TAL-API-01 — actor filter behavior for non-existent values not explicitly stated (Low)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-17)
