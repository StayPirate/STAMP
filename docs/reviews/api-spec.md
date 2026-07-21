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

**Category**: Boundary condition
**Status**: OPEN

The Pagination section states `per_page (int, default: 20, max: 100)` and `page (int, default: 1)` but does not specify the behavior when these constraints are violated (`per_page > 100`, `per_page < 1`, `page < 1`, `page = 0`). Feature specs are demonstrably inconsistent on this point: `docs/features/packages/package-model.md` specifies that `page < 1 or per_page < 1 or per_page > 100` returns `422 VALIDATION_ERROR`, while other endpoints silently clamp `per_page` to 100. An implementer of a new list endpoint has two plausible choices (strict rejection vs. lenient clamping) with no cross-cutting rule to resolve the ambiguity, which will keep producing inconsistent behavior across endpoints as new ones are added.

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

**Category**: Ambiguous rule
**Status**: OPEN

The example response shows `{"field": "field_name", "message": "Validation error message"}`, while the Global Responses note states "the 422 response uses Pydantic's native format with the errors array populated with field-level details." This is an internal contradiction: Pydantic v2's native validation error format uses `loc`/`msg`/`type` keys, not `field`/`message`. An implementer cannot tell whether to produce the shape shown in the example or Pydantic's actual native shape, and API client developers have no definitive schema to parse field-level errors programmatically (e.g., whether `field` is a simple name or a dotted path for nested objects/array items).

### APIS-API-02 — `CELERY_ENQUEUE_FAILED` violates the stated Infrastructure Dependency Errors naming pattern (Medium)

**Category**: Internal contradiction
**Status**: OPEN

The Infrastructure Dependency Errors (HTTP 503) section declares the naming pattern as `<DEPENDENCY>_UNAVAILABLE`, but the example table lists `CELERY_ENQUEUE_FAILED`, which does not follow that pattern (it would need to be `CELERY_UNAVAILABLE` to conform). The section simultaneously establishes a strict pattern and provides a counter-example, leaving implementers uncertain whether the pattern is mandatory or merely a loose guideline for 503 errors.

### APIS-API-03 — Infrastructure dependency error code prefixes missing from the Error Code Categories table (Medium)

**Category**: Incomplete enumeration
**Status**: OPEN

The Error Code Categories section states "every new error introduced in the codebase MUST have a corresponding code with the appropriate prefix," and the prefix table enumerates closed categories (`AUTH_*`, `TICKET_*`, `CVE_*`, etc.). The Infrastructure Dependency Errors section introduces codes such as `REDIS_UNAVAILABLE`, `SMELT_UNAVAILABLE`, and `PROVISIONING_UNAVAILABLE` whose prefixes have no entry in that table. An implementer adding a new infrastructure error (e.g., for a future Bugzilla integration) has no documented prefix category to follow and must either violate the stated rule or invent an undocumented one.
