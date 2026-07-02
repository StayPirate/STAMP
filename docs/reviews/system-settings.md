# Review: system-settings

**Spec**: `docs/features/platform/system-settings.md`
**Last reviewed**: 2026-07-02
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### ADM-GAP-01 — Task enqueue failure behavior unspecified (High)

**Category**: Error and failure paths
**Status**: OPEN

The PATCH endpoint commits the setting change and `SettingAuditEvent` in one database transaction, then enqueues the batch recalculation Celery task. If the Redis broker is unavailable at enqueue time, the spec does not define whether: (a) the setting change is rolled back, (b) the setting persists and the API returns a 503 indicating partial success, or (c) the failure is silently swallowed. If the setting commits without the batch task, all active tickets retain severity and eligibility derived from the old CVSS version until something else triggers recalculation — an inconsistent state with no documented recovery path.

### ADM-GAP-02 — No-op behavior when setting value is unchanged (Medium)

**Category**: Idempotency
**Status**: OPEN

The spec states "Triggers recalculation for all active tickets as a background task" unconditionally. When an admin PATCHes `{"default_cvss_version": "3.1"}` and the current value is already `"3.1"`, the spec does not specify whether: (a) the batch task is triggered (expensive no-op), (b) a `SettingAuditEvent` is created with `old_value == new_value`, or (c) the operation short-circuits. The audit-trail-infrastructure spec's cross-cutting rule ("no audit event for idempotent no-ops") implies option (c), but system-settings.md contradicts this by stating the trigger unconditionally. One clarifying sentence would eliminate the ambiguity.

### ADM-GAP-03 — PATCH success HTTP status code not explicitly stated (Low)

**Category**: Boundary conditions
**Status**: OPEN

The PATCH endpoint specifies error codes (403, 422) but does not explicitly state the success HTTP status code. Other specs in the project (e.g., ticket-mutations, package-service) consistently declare success codes. An implementer could choose 200 (standard PATCH-with-body), 202 (async side effects), or 204 (no body). The response description ("the updated settings object") implies 200, but stating it explicitly maintains consistency with other endpoint definitions in the project.

---

## Coherence

### ADM-COH-01 — Configuration reference attribution for default_cvss_version (Low)

**Category**: Cross-reference consistency
**Status**: OPEN

In `docs/configuration.md`, the "Defined in" column for `default_cvss_version` points to `docs/features/tickets/cvss-scoring.md`. However, the authoritative definition of this runtime setting — its properties table, allowed values, bootstrap mechanism, CRUD API, and audit log — lives in `docs/features/platform/system-settings.md`. The `cvss-scoring.md` spec itself defers to system-settings.md in its cross-references. Since `configuration.md` states "Each setting is defined authoritatively in the feature specification linked in the 'Defined in' column", the link should point to `system-settings.md`.

---

## Design

_No issues identified._

---

## Security

_No issues identified._

---

## API Conventions

### ADM-API-01 — Non-descriptive endpoint heading for audit-log endpoint (Low)

**Category**: Endpoint Permission Map completeness
**Status**: OPEN

The audit-log endpoint's definition heading is `### API` (producing anchor `#api`), which is excessively generic. The RBAC Endpoint Permission Map links to `[system-settings](../platform/system-settings.md#api)` — technically resolves but provides poor readability and could become ambiguous if more API sections are added. Other specs use descriptive headings like `### List Fetcher Runs` or `### Get Settings Audit Log`.

### ADM-API-02 — PATCH mutation pattern for CVSS version change with massive side effects (High)

**Status**: RESOLVED — Added explicit "Note on PATCH with side effects" paragraph to `docs/features/platform/system-settings.md` justifying the deviation: the endpoint is semantically a configuration field update with instant response, and the recalculation chain is an asynchronous Celery task that does not block the client. (2026-05-06)

### ADM-API-03 — Missing 401/403 error responses for authenticated endpoints (Medium)

**Status**: RESOLVED — Added blanket auth note at the top of the API Endpoints section covering both endpoints, and added explicit 403 rows to the PATCH error response table. (2026-05-06)

### ADM-API-04 — Task progress tracking for background recalculation unspecified (Low)

**Status**: RESOLVED — Removed "plus a task status indicator if recalculation is in progress" from the PATCH response description — no progress tracking is needed. The response simply returns the updated settings. The recalculation happens asynchronously via Celery with no client-side polling. (2026-05-06)
