# Review: logging

**Spec**: `docs/features/platform/logging.md`
**Last reviewed**: 2026-07-21
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### LOG-GAP-01 — Ambiguous scope of correlation context clearing in task_prerun (Medium)

**Status**: RESOLVED — Spec updated: Reset requirement now explicitly enumerates all three ContextVars (request_id, celery_task_id, fetcher_run_id) cleared by task_prerun (2026-07-21)

### LOG-GAP-02 — Structlog behavior unspecified when service code is invoked from CLI processes (Low)

**Status**: RESOLVED — Spec updated: added minimal structlog configuration requirement for CLI processes in Scope section (2026-07-21)

---

## Coherence

### LOG-COH-01 — Stale cross-reference to removed "end-to-end debugging" phrase in api-spec.md (Low)

**Status**: RESOLVED — Spec updated: replaced stale "end-to-end debugging" cross-reference with current "request-scoped debugging" wording from api-spec.md (2026-07-21)

---

## Design

### LOG-DES-01 — No log volume guidance for high-throughput fetchers (Medium)

**Status**: RESOLVED — Spec updated: added batch log volume guideline (per-item success at DEBUG, aggregates at INFO) in Log Levels section (2026-07-21)

---

## Security

_(no findings — section is clean)_

---

## API Conventions

_(no findings — section is clean)_
