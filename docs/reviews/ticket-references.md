# Review: ticket-references

**Spec**: `docs/features/tickets/ticket-references.md`
**Last reviewed**: 2026-05-20
**Reviewers**: Gap Analysis, Coherence, API Conventions

---

## Gap Analysis

### TRF-GAP-01 — Mutability guard applicability unspecified (Medium)

**Category**: Error paths
**Status**: OPEN

The three mutation endpoints (POST, PATCH, DELETE on references) do not state
whether they are subject to the `require_ticket_mutable` guard. The
`tickets.md` spec declares the guard applies to "all endpoints that modify
ticket data," but the ticket-references spec characterizes references as
"supplementary external metadata" (audit trail section). An implementer could
reasonably go either way. The spec should explicitly declare whether reference
mutations are subject to the mutability guard or exempt.

### TRF-GAP-02 — Upsert source ownership between fetchers (Medium)

**Category**: Error paths
**Status**: OPEN

The NVD fetcher adds `https://example.com/advisory` with
`source = "sync_cves_nvd"`. Later, the MITRE fetcher encounters the same URL
for the same CVE and upserts it, overwriting `source` to `"sync_cves_mitre"`.
This means NVD's stale cleanup no longer recognizes the reference, and MITRE's
cleanup claims ownership. If the URL later disappears from MITRE but remains
in NVD, MITRE deletes it and NVD re-creates it — causing unnecessary churn.
The spec should clarify whether `source` should be updated when the record
already exists, or if first-writer's source is preserved.

### TRF-GAP-03 — Concurrent fetcher overrides manual reference (Medium)

**Category**: Concurrency
**Status**: OPEN

A VA adds a manual reference with a URL. Concurrently, a fetcher processes
the same CVE and encounters the same URL. The upsert would UPDATE `source`
from `"manual"` to the fetcher name — silently converting a manual reference
to an automatic one. The spec should specify that fetcher upserts do NOT
overwrite `source = "manual"` records (manual references take precedence).

### TRF-GAP-04 — Soft-deleted ticket and fetcher stale cleanup (Medium)

**Category**: Data lifecycle
**Status**: OPEN

The `tickets.md` spec states soft-deleted tickets are "excluded from all
background processing." However, the fetcher stale cleanup processes CVEs
(not tickets), and reference cleanup happens as a side effect of CVE
processing. If the fetcher processes a CVE whose ticket has been soft-deleted,
it might delete references from a ticket that an Admin intends to preserve
intact. The spec should confirm that fetcher stale cleanup skips references
on soft-deleted tickets.

### TRF-GAP-05 — Deleting an automatic reference is transient (Medium)

**Category**: User-facing scenarios
**Status**: OPEN

A VA deletes an automatic reference (e.g., a noisy vendor advisory from NVD).
On the next sync, the fetcher encounters the same URL and re-creates it via
upsert. The VA's deletion is effectively undone. The spec documents this
scenario for URL editing (Note on automatic references in Update Reference)
but not for deletion. The spec should document that automatic references
cannot be permanently deleted while the source data still includes them, and
potentially suggest a future "hide" mechanism.

### TRF-GAP-06 — URL scheme validation undefined (Medium)

**Category**: Validation
**Status**: OPEN

The spec says `url` "must be a valid URL" but does not define which URL
schemes are accepted. Allowing arbitrary schemes (especially `javascript:` or
`data:`) could have security implications when rendered as clickable links.
The spec should define accepted schemes (recommend `http` and `https` only).

### TRF-GAP-07 — Stale cleanup atomicity unspecified (Low)

**Category**: Error paths
**Status**: OPEN

The ingestion flow (upsert + stale cleanup) does not state whether it is
wrapped in a single database transaction. If not atomic, a database error
mid-cleanup would leave some stale references while new ones are already
inserted. The spec should state the atomicity expectation.

### TRF-GAP-08 — Maximum length for url and title fields (Low)

**Category**: Boundary conditions
**Status**: OPEN

Both `url` and `title` are `TEXT` columns (unbounded). The spec defines no
maximum length for request body fields. An extremely long URL or title could
cause storage or display issues. Explicit limits would prevent abuse.

### TRF-GAP-09 — Empty or whitespace-only title handling (Low)

**Category**: Boundary conditions
**Status**: OPEN

The spec says `title` is optional (nullable) but does not specify whether an
empty string or whitespace-only string should be treated as NULL, rejected,
or stored as-is.

### TRF-GAP-10 — Tags array constraints for fetcher-created references (Low)

**Category**: Boundary conditions
**Status**: OPEN

There is no stated maximum on the number of elements in the `tags` array, nor
on the length of each tag string. Since tags come from external source data,
malformed or extremely long values could be persisted without validation.

### TRF-GAP-11 — Source filter with unknown value (Low)

**Category**: Boundary conditions
**Status**: OPEN

A client requests `?source=nonexistent_fetcher`. The spec does not state
whether this returns an empty list (200 OK) or a validation error. `source`
is freeform VARCHAR, not an enum — the spec should clarify the behavior.

### TRF-GAP-12 — Update endpoint does not allow modifying tags (Low)

**Category**: User-facing scenarios
**Status**: OPEN

The PATCH request body only accepts `url` and `title`. Tags cannot be set on
manual references at creation (always NULL) or added via update. If tags are
meant to be fetcher-only, the spec should state this explicitly.

### TRF-GAP-13 — URL normalization strategy unspecified (Low)

**Category**: Validation
**Status**: OPEN

The unique constraint operates on raw text. Semantically equivalent URLs
(e.g., different casing of host, trailing slash) are stored as separate
references. The spec should state that comparison is exact (case-sensitive, no
normalization).

---

## Coherence

### TRF-COH-01 — Missing TICKET_NOT_MUTABLE guard on mutation endpoints (Medium)

**Status**: OPEN — Cross-reference of TRF-GAP-01. The `tickets.md` spec
(lines 1056–1076) declares the guard applies to "all endpoints that modify
ticket data." The three reference mutation endpoints would allow modifications
on Ignored/Duplicated tickets unless the guard is applied.

### TRF-COH-02 — Inconsistent 404 codes between Update/Delete and List/Add (Low)

**Category**: Cross-spec inconsistency
**Status**: OPEN

Update Reference and Delete Reference use `RESOURCE_NOT_FOUND` with condition
"Ticket or reference not found" — conflating two distinct failure modes. The
ticket-not-found case is handled by the scoped Ticket Accessibility Check
dependency and should use `TICKET_NOT_FOUND` (or be removed from the table
entirely since it's a scoped response). `RESOURCE_NOT_FOUND` should only apply
to "reference not found within a valid ticket."

---

## API Conventions

### TRF-API-01 — Update/Delete conflate TICKET_NOT_FOUND and RESOURCE_NOT_FOUND (Medium)

**Status**: OPEN — Cross-reference of TRF-COH-02. The error tables for Update
and Delete should separate the two failure modes or rely on the scoped
dependency for the ticket-not-found case.

### TRF-API-02 — Mutability guard applicability ambiguity (Low)

**Category**: Ambiguity
**Status**: OPEN

The spec does not mention whether the Manual-Zone Mutability Guard
(`TICKET_NOT_MUTABLE`, `api-spec.md` lines 294–314) applies to reference
mutations. Since references are "supplementary external metadata," it could
be argued either way. The spec should explicitly state the applicability.
