# Review: api-spec

**Spec**: `docs/api-spec.md`
**Last reviewed**: 2026-07-27
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### APIS-GAP-04 — No tie-breaking convention for pagination with non-unique sort keys (Medium)

**Status**: RESOLVED — Added cross-cutting "Deterministic Pagination Ordering" subsection to docs/api-spec.md Sorting section mandating secondary sort by `id`; replaced per-endpoint secondary sort details in package-model.md and package-service.md with references to the new convention (2026-07-27)

### APIS-GAP-05 — Enum filter value format ambiguity (comma-separated vs repeatable) (Medium)

**Status**: RESOLVED — Rewrote Enum Filter Validation section in docs/api-spec.md to define repeatable format as the exclusive multi-value standard, explicitly disallow comma-separated format, and add opt-in rule (single-value by default, endpoints must declare `repeatable`) (2026-07-27)

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

**Status**: RESOLVED — Added `status` row to the Semantic Sort Fields table in docs/api-spec.md with lifecycle-based ranking (New < Analysis < Analyzed < Resolved < Ignored < Duplicated); annotated `status` in tickets.md sort_by values with "semantic ordering, see Sorting" (2026-07-27)

### APIS-DES-02 — INTERNAL_ERROR code sits outside the prefix-based error code taxonomy (Low)

**Status**: RESOLVED — Cross-agent duplicate of APIS-API-04 (2026-07-27)

---

## Security

_No findings._

---

## API Conventions

### APIS-API-04 — INTERNAL_ERROR not registered in Error Code Categories table (Medium)

**Status**: RESOLVED — Added `INTERNAL_*` prefix row (domain: Framework) to Error Code Categories table; migrated `RECALC_ALREADY_IN_PROGRESS` to `CVSS_RECALC_ALREADY_IN_PROGRESS` under `CVSS_*` prefix in api-spec.md and system-settings.md (2026-07-27)

### APIS-API-05 — AUTH_SSO_UNAVAILABLE doesn't follow DEPENDENCY_UNAVAILABLE naming pattern (Medium)

**Status**: RESOLVED — Renamed `AUTH_SSO_UNAVAILABLE` to `SSO_UNAVAILABLE` in api-spec.md (Infrastructure Dependency Errors table + Error Code Categories examples) and sso-authentication.md (all 3 occurrences) (2026-07-27)

### APIS-API-01 — `errors` array element schema underspecified (Medium)

**Status**: RESOLVED — Aligned errors array example with Pydantic v2's native validation error format (loc/msg/type), which FastAPI produces automatically; added explicit element schema documentation (2026-07-21)

### APIS-API-02 — `CELERY_ENQUEUE_FAILED` violates the stated Infrastructure Dependency Errors naming pattern (Medium)

**Status**: RESOLVED — Renamed CELERY_ENQUEUE_FAILED to CELERY_UNAVAILABLE across all specs to conform to the <DEPENDENCY>_UNAVAILABLE naming pattern for Infrastructure Dependency Errors (2026-07-21)

### APIS-API-03 — Infrastructure dependency error code prefixes missing from the Error Code Categories table (Medium)

**Status**: RESOLVED — Added <DEPENDENCY>_* prefix entry to the Error Code Categories table documenting the pattern used by Infrastructure Dependency Errors (2026-07-21)
