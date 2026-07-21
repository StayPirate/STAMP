# Review: api-spec

**Spec**: `docs/api-spec.md`
**Last reviewed**: 2026-07-21
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### APIS-GAP-01 — No validation/sanitization rule for client-supplied `X-Request-ID` (Medium)

**Status**: RESOLVED — Validation rules added to api-spec.md Request Tracing section: bounded charset `[A-Za-z0-9._-]`, max 128 chars, invalid/duplicate/empty values fall back to a generated UUIDv4 (2026-07-21)

### APIS-GAP-02 — Ambiguous scope of "end-to-end debugging" wording (Low)

**Status**: RESOLVED — Wording in api-spec.md Request Tracing reformulated to scope propagation explicitly to synchronous request processing, with a cross-reference to logging.md for scope boundaries (2026-07-21)

### APIS-GAP-03 — Pagination parameter violation behavior unspecified (Medium)

**Status**: RESOLVED — Added explicit cross-cutting pagination violation behavior rule (422 VALIDATION_ERROR for out-of-bounds page/per_page; empty data array for page beyond last page) to docs/api-spec.md Pagination section (2026-07-21)

---

## Coherence

_No findings._

---

## Design

_No findings._

---

## Security

_No findings._

---

## API Conventions

### APIS-API-01 — `errors` array element schema underspecified (Medium)

**Status**: RESOLVED — Aligned errors array example with Pydantic v2's native validation error format (loc/msg/type), which FastAPI produces automatically; added explicit element schema documentation (2026-07-21)

### APIS-API-02 — `CELERY_ENQUEUE_FAILED` violates the stated Infrastructure Dependency Errors naming pattern (Medium)

**Status**: RESOLVED — Renamed CELERY_ENQUEUE_FAILED to CELERY_UNAVAILABLE across all specs to conform to the <DEPENDENCY>_UNAVAILABLE naming pattern for Infrastructure Dependency Errors (2026-07-21)

### APIS-API-03 — Infrastructure dependency error code prefixes missing from the Error Code Categories table (Medium)

**Status**: RESOLVED — Added <DEPENDENCY>_* prefix entry to the Error Code Categories table documenting the pattern used by Infrastructure Dependency Errors (2026-07-21)
