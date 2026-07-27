# Review: api-spec

**Spec**: `docs/api-spec.md`
**Last reviewed**: 2026-07-27
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### APIS-GAP-04 — No tie-breaking convention for pagination with non-unique sort keys (Medium)

**Category**: Boundaries
**Status**: OPEN

The Sorting section defines `sort_by` and `sort_order` with a default of `sort_by=created_at, sort_order=desc`. The Pagination section defines `page` and `per_page`. Neither section addresses what happens when multiple resources share the same value for the sorted field. During batch CVE ingestion, 50 tickets could be created in the same second, all sharing identical `created_at` timestamps. A client paginating with `sort_by=created_at&per_page=20` receives page 1 with 20 tickets. On the next request for page 2, the database may return a different partition of the 50 tickets (PostgreSQL does not guarantee stable ordering for equal values without a tiebreaker). The client sees duplicates on page 2 that were already on page 1, and misses other tickets entirely. A cross-cutting convention (e.g., "all paginated endpoints MUST include a deterministic secondary sort by `id`") would ensure consistent behavior across all list endpoints.

### APIS-GAP-05 — Enum filter value format ambiguity (comma-separated vs repeatable) (Medium)

**Category**: Boundaries
**Status**: OPEN

The Enum Filter Validation section states: "When a filter parameter accepts enum values (comma-separated or repeatable), invalid values are silently ignored." The phrase "comma-separated or repeatable" is ambiguous. It could mean: (a) both formats are always accepted on any enum filter parameter, or (b) the format choice is per-endpoint. The tickets.md spec declares `status` as "repeatable", suggesting format is per-endpoint. But the cross-cutting spec doesn't establish whether endpoints that say "repeatable" also accept comma-separated values. An implementer of a new list endpoint must decide: should `?status=new,analysis` be accepted (split on comma) or treated as a single invalid value `"new,analysis"` (silently ignored, producing an empty result set)? Two implementers working on different endpoints could make opposite choices, creating an inconsistent API surface.

### APIS-GAP-01 — No validation/sanitization rule for client-supplied `X-Request-ID` (Medium)

**Status**: RESOLVED — Validation rules added to api-spec.md Request Tracing section: bounded charset `[A-Za-z0-9._-]`, max 128 chars, invalid/duplicate/empty values fall back to a generated UUIDv4 (2026-07-21)

### APIS-GAP-02 — Ambiguous scope of "end-to-end debugging" wording (Low)

**Status**: RESOLVED — Wording in api-spec.md Request Tracing reformulated to scope propagation explicitly to synchronous request processing, with a cross-reference to logging.md for scope boundaries (2026-07-21)

### APIS-GAP-03 — Pagination parameter violation behavior unspecified (Medium)

**Status**: RESOLVED — Added explicit cross-cutting pagination violation behavior rule (422 VALIDATION_ERROR for out-of-bounds page/per_page; empty data array for page beyond last page) to docs/api-spec.md Pagination section (2026-07-21)

### APIS-GAP-06 — INTERNAL_ERROR code not registered in Error Code Categories table (Low)

**Status**: RESOLVED — Cross-agent duplicate of APIS-API-04 (2026-07-27)

---

## Coherence

_No findings._

### APIS-COH-01 — INTERNAL_ERROR omitted from Error Code Categories despite prefix rule (Low)

**Status**: RESOLVED — Cross-agent duplicate of APIS-API-04 (2026-07-27)

### APIS-COH-02 — AUTH_SSO_UNAVAILABLE unregistered in Error Code Categories table (Low)

**Status**: RESOLVED — Cross-agent duplicate of APIS-API-05 (2026-07-27)

---

## Design

### APIS-DES-01 — Semantic Sort Fields table incomplete — status has lifecycle semantics but no defined ordering (Medium)

**Category**: Design
**Status**: OPEN

The Semantic Sort Fields section defines ordinal ranking only for `severity`. However, `tickets.md` allows `sort_by=status` without a semantic ordering annotation. Without a defined rank, status sorting defaults to alphabetical (Analysis, Analyzed, Duplicated, Ignored, New, Resolved), which is meaningless from a workflow perspective — `New` sorts after `Ignored`, and `Duplicated` sorts between `Analyzed` and `Ignored`. A VA sorting tickets by lifecycle progression would get a confusing jumble. The api-spec should add a lifecycle-based ranking for status to the Semantic Sort Fields table (e.g., New < Analysis < Analyzed < Resolved < Ignored < Duplicated). Any reasonable ordering is better than alphabetical.

### APIS-DES-02 — INTERNAL_ERROR code sits outside the prefix-based error code taxonomy (Low)

**Status**: RESOLVED — Cross-agent duplicate of APIS-API-04 (2026-07-27)

---

## Security

_No findings._

---

## API Conventions

### APIS-API-04 — INTERNAL_ERROR not registered in Error Code Categories table (Medium)

**Category**: API Conventions
**Status**: OPEN

`INTERNAL_ERROR` is used in the Global Responses table and the Response Applicability Derivation table, making it a fundamental error code returned by every endpoint. However, it does not appear under any prefix in the Error Code Categories table. No `INTERNAL_*` or `SYSTEM_*` prefix category exists. The spec rule states "Every new error introduced in the codebase MUST have a corresponding code with the appropriate prefix," but `INTERNAL_ERROR` has no assigned prefix. Fix by either adding an `INTERNAL_*` prefix row to the Error Code Categories table, or adding an explicit note that `INTERNAL_ERROR` is a framework-level code exempt from the prefix rule.

### APIS-API-05 — AUTH_SSO_UNAVAILABLE doesn't follow DEPENDENCY_UNAVAILABLE naming pattern (Medium)

**Category**: API Conventions
**Status**: OPEN

The Infrastructure Dependency Errors section defines the pattern as `<DEPENDENCY>_UNAVAILABLE` with HTTP 503, showing examples: `REDIS_UNAVAILABLE`, `SMELT_UNAVAILABLE`, `CELERY_UNAVAILABLE`, `PROVISIONING_UNAVAILABLE`. All follow the `<DEPENDENCY>_UNAVAILABLE` pattern without a domain prefix. However, `AUTH_SSO_UNAVAILABLE` uses the `AUTH_` domain prefix, breaking the pattern (it should be `SSO_UNAVAILABLE` per the convention). Additionally, `AUTH_SSO_UNAVAILABLE` is not listed in the `AUTH_*` row examples, so it falls through a categorization gap. This creates ambiguity: when adding a new infrastructure dependency error, should the implementer use the `<DEPENDENCY>_UNAVAILABLE` pattern or add a domain prefix? Fix by either renaming to `SSO_UNAVAILABLE` for consistency, or documenting that dependencies within an existing domain may use the domain prefix.

### APIS-API-01 — `errors` array element schema underspecified (Medium)

**Status**: RESOLVED — Aligned errors array example with Pydantic v2's native validation error format (loc/msg/type), which FastAPI produces automatically; added explicit element schema documentation (2026-07-21)

### APIS-API-02 — `CELERY_ENQUEUE_FAILED` violates the stated Infrastructure Dependency Errors naming pattern (Medium)

**Status**: RESOLVED — Renamed CELERY_ENQUEUE_FAILED to CELERY_UNAVAILABLE across all specs to conform to the <DEPENDENCY>_UNAVAILABLE naming pattern for Infrastructure Dependency Errors (2026-07-21)

### APIS-API-03 — Infrastructure dependency error code prefixes missing from the Error Code Categories table (Medium)

**Status**: RESOLVED — Added <DEPENDENCY>_* prefix entry to the Error Code Categories table documenting the pattern used by Infrastructure Dependency Errors (2026-07-21)
