# Review: system-settings

**Spec**: `docs/features/platform/system-settings.md`
**Last reviewed**: 2026-07-02
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### ADM-GAP-01 — Task enqueue failure behavior unspecified (High)

**Category**: Error and failure paths
**Status**: RESOLVED — The simplified design uses commit-first ordering with a recalculation slot (Redis SET NX) as a liveness probe before the commit. If Redis is unreachable, the PATCH returns 503 and nothing is committed. If the enqueue fails after commit (transient broker failure in the micro-window), the PATCH returns 200 with `recalculation_scheduled: false` and the admin uses the dedicated re-run endpoint (`POST /api/v1/admin/settings/default-cvss-version/recalculate`) to trigger the batch manually. (2026-07-03)

### ADM-GAP-02 — No-op behavior when setting value is unchanged (Medium)

**Category**: Idempotency
**Status**: RESOLVED — The PATCH endpoint now includes an explicit no-op check (step 2): if the current value equals the new value, the endpoint returns 200 immediately with no audit event and no batch task. This is consistent with the audit-trail-infrastructure cross-cutting rule for idempotent no-ops. (2026-07-03)

### ADM-GAP-03 — PATCH success HTTP status code not explicitly stated (Low)

**Category**: Boundary conditions
**Status**: RESOLVED — The PATCH response now explicitly states "Response (200 OK)" with the full response schema including the `recalculation_scheduled` field. (2026-07-03)

---

## Coherence

### ADM-COH-01 — Configuration reference attribution for default_cvss_version (Low)

**Category**: Cross-reference consistency
**Status**: RESOLVED — Updated the "Defined in" column for `default_cvss_version` in `docs/configuration.md` to point to `docs/features/platform/system-settings.md`. (2026-07-03)

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
**Status**: RESOLVED — Heading renamed from `### API` to `### List Settings Audit Events` (anchor: `#list-settings-audit-events`). RBAC Endpoint Permission Map link updated accordingly. (2026-07-03)

### ADM-API-02 — PATCH mutation pattern for CVSS version change with massive side effects (High)

**Status**: RESOLVED — Added explicit "Note on PATCH with side effects" paragraph to `docs/features/platform/system-settings.md` justifying the deviation: the endpoint is semantically a configuration field update with instant response, and the recalculation chain is an asynchronous Celery task that does not block the client. (2026-05-06)

### ADM-API-03 — Missing 401/403 error responses for authenticated endpoints (Medium)

**Status**: RESOLVED — Added blanket auth note at the top of the API Endpoints section covering both endpoints, and added explicit 403 rows to the PATCH error response table. (2026-05-06)

### ADM-API-04 — Task progress tracking for background recalculation unspecified (Low)

**Status**: RESOLVED — Removed "plus a task status indicator if recalculation is in progress" from the PATCH response description — no progress tracking is needed. The response simply returns the updated settings. The recalculation happens asynchronously via Celery with no client-side polling. (2026-05-06)
