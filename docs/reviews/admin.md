# Review: admin

**Spec**: `docs/features/platform/admin.md`
**Last reviewed**: 2026-05-06
**Reviewers**: API Conventions

---

## Gap Analysis

_Not yet reviewed._

---

## Coherence

_Not yet reviewed._

---

## Design

_Not yet reviewed._

---

## Security

_Not yet reviewed._

---

## API Conventions

### ADMIN-API-01 — PATCH mutation pattern for CVSS version change with massive side effects (High)

**Status**: RESOLVED — Added explicit "Note on PATCH with side effects" paragraph to `docs/features/platform/admin.md` justifying the deviation: the endpoint is semantically a configuration field update with instant response, and the recalculation cascade is an asynchronous Celery task that does not block the client. (2026-05-06)

### ADMIN-API-02 — Missing 401/403 error responses for authenticated endpoints (Medium)

**Status**: RESOLVED — Added blanket auth note at the top of the API Endpoints section covering both endpoints, and added explicit 401/403 rows to the PATCH error response table in `docs/features/platform/admin.md`. (2026-05-06)

### ADMIN-API-03 — Task progress tracking for background recalculation unspecified (Low)

**Status**: RESOLVED — Removed "plus a task status indicator if recalculation is in progress" from the PATCH response description — no progress tracking is needed. The response simply returns the updated settings. The UI confirmation dialog was simplified to warn the admin that all products on open tickets will be re-evaluated. The recalculation happens asynchronously via Celery with no client-side polling. (2026-05-06)
