# Review: ticket-audit-log

**Spec**: `docs/features/tickets/ticket-audit-log.md`
**Last reviewed**: 2026-05-16
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

**Category**: Boundary conditions
**Status**: OPEN

If a user passes from_date=2025-03-15 (date without time), is this interpreted as midnight UTC? If they pass 2025-03-15T10:00:00+02:00, is the timezone offset respected? The spec says "ISO 8601" but doesn't specify timezone interpretation rules for the filter parameters.

### TAL-GAP-06 — search filter on NULL comment field (Low)

**Category**: Boundary conditions
**Status**: OPEN

Many event types specify comment as NULL. Using the search filter will exclude all events with NULL comments. This is technically correct SQL behavior (ILIKE on NULL yields NULL, not TRUE) but could confuse users who expect to find events via search when the relevant context is in other fields like old_value or new_value rather than comment.

### TAL-GAP-07 — Concurrent mutations producing conflicting audit events (Low)

**Category**: Temporal/concurrency
**Status**: OPEN

Two VAs concurrently change a ticket's status. Both read old_value as "New" and write events with old_value: "New". One transaction will succeed; the other may also succeed (if no optimistic lock exists), creating two events where the second event's old_value is stale. The spec doesn't address whether optimistic locking or serializable isolation is expected to prevent this.

---

## Coherence

_No findings — clean review._

---

## Design

### TAL-DES-01 — Overloaded comment field as structured data carrier (Medium)

**Category**: Maintainability
**Status**: OPEN

A package named openssl:fips (containing a colon) would make the comment openssl:fips:SUSE:SLE-15-SP6:Update ambiguous for parsing. The comment field is being used both as a human-readable note and as a structured data carrier with colon-delimited values. Consider adding a detail JSONB column (like IdentityAuditEvent already has) for structured context, keeping comment purely for human-readable notes.

### TAL-DES-02 — track_released vs track_status_changed overlap (Low)

**Status**: RESOLVED — Cross-agent duplicate of TAL-GAP-02 (2026-05-16)

### TAL-DES-03 — Unbounded ILIKE search on comment (Medium)

**Category**: Scalability
**Status**: OPEN

ILIKE '%term%' with a leading wildcard cannot use a B-tree index. For high-activity tickets with 10,000+ events, this could result in slow queries. The per-ticket scoping provides some mitigation, but the spec should acknowledge this limitation or suggest a mitigation path (e.g., pg_trgm GIN index) for large-scale deployments.

### TAL-DES-04 — Indefinite retention with no archival path (Low)

**Category**: Scalability
**Status**: OPEN

Over years, the audit event table will grow to tens of millions of rows. The spec defines no archival, partitioning, or retention policy. Acceptable for initial deployment but worth noting as a future concern that should be addressed before the table becomes a performance bottleneck.

---

## Security

### TAL-SEC-01 — Public access to full audit history (Medium)

**Category**: Authorization gap
**Status**: OPEN

The audit log endpoint is publicly accessible. While ticket data itself is public, the audit log exposes actor details (username, full_name, UUID) and operational patterns (who changed what, when). This information could aid reconnaissance by revealing organizational structure, work patterns, and individual responsibilities.

### TAL-SEC-02 — ILIKE search without length limit (Low)

**Category**: Input validation
**Status**: OPEN

Without a maximum length constraint on the search query string, very long patterns could cause inefficient regex compilation and query execution. The spec should define a maximum search string length to prevent abuse.

### TAL-SEC-03 — No rate limiting specified (Low)

**Category**: Resource exhaustion
**Status**: OPEN

The public endpoint with text search capability could be abused for user enumeration (searching for actor names) or denial of service via expensive ILIKE queries. The spec should reference the platform's rate limiting strategy or define endpoint-specific limits.

---

## API Conventions

### TAL-API-01 — actor filter behavior for non-existent values not explicitly stated (Low)

**Category**: Ambiguity
**Status**: OPEN

The spec doesn't explicitly state what happens when the actor filter value doesn't match any user. Per api-spec.md convention, non-matching filter values produce empty result sets, but this isn't restated in the spec. Minor since covered by general convention, but could cause confusion for implementers unfamiliar with the general rules.
