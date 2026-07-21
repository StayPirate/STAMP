# Review: logging

**Spec**: `docs/features/platform/logging.md`
**Last reviewed**: 2026-07-21
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### LOG-GAP-01 — Ambiguous scope of correlation context clearing in task_prerun (Medium)

**Category**: Temporal and concurrency gaps
**Status**: OPEN

The spec states (Reset requirement): "task_prerun MUST unconditionally clear any existing correlation context before binding the new celery_task_id." The phrase "any existing correlation context" could mean: (A) clear ALL three correlation variables (request_id, celery_task_id, fetcher_run_id), or (B) clear only celery_task_id before re-binding it. Reading (A) is the natural interpretation, but the subordinate clause "before binding the new celery_task_id" could be narrowly read as scoping the clearing to just celery_task_id.

Scenario: a fetcher task runs, BaseFetcher.run() binds fetcher_run_id, then execute() raises an unhandled exception that also prevents run()'s own finally block from resetting fetcher_run_id. task_postrun fires and resets celery_task_id. The next task in the same Celery prefork worker starts. If task_prerun under reading (B) only clears celery_task_id, the stale fetcher_run_id from the crashed fetcher leaks into all log records of the new task, producing incorrect correlation data.

The spec should explicitly enumerate which ContextVars are cleared by task_prerun (all three: request_id, celery_task_id, fetcher_run_id).

### LOG-GAP-02 — Structlog behavior unspecified when service code is invoked from CLI processes (Low)

**Category**: Boundary conditions
**Status**: OPEN

The spec states (Scope of this pipeline): "CLI (Click) processes do not invoke it — they rely on the Python stdlib logging default (stderr)." Meanwhile, docs/conventions.md (Logging) establishes that "Application code obtains a structlog logger bound to the module and logs via its standard methods."

If a CLI command calls a shared service or utility function that uses structlog.get_logger(), and the structlog pipeline has not been configured (because CLI processes skip configuration), the log output format depends on structlog's unconfigured default behavior — which produces output in neither the specified "json" nor "console" format.

Scenario: a CLI command like "sentinel manage-user create" calls user_service functions that contain structlog log statements. These statements would emit log records in structlog's raw default format (a plain key=value representation), which differs from both configured renderers and could confuse an operator reading the output.

The spec should state whether CLI-invoked structlog calls are expected to use stdlib fallback (and if so, how structlog routes to stdlib without configuration), or whether a minimal structlog configuration should be applied for CLI processes.

---

## Coherence

### LOG-COH-01 — Stale cross-reference to removed "end-to-end debugging" phrase in api-spec.md (Low)

**Category**: Terminology issues
**Status**: OPEN

logging.md says: 'The "end-to-end debugging" wording in docs/api-spec.md (Request Tracing) describes the synchronous request-processing lifecycle, not asynchronous work it may enqueue.' However, api-spec.md no longer contains the phrase "end-to-end debugging" — it was replaced with "request-scoped debugging" as part of the APIS-GAP-02 resolution (see docs/reviews/api-spec.md). The logging spec's substantive point remains correct (request_id applies only to synchronous processing), but it quotes a phrase that no longer exists in the referenced document. The fix is to update logging.md to reference the current wording: 'The "request-scoped debugging" wording in docs/api-spec.md'.

---

## Design

### LOG-DES-01 — No log volume guidance for high-throughput fetchers (Medium)

**Category**: Scalability and maintainability
**Status**: OPEN

The spec defines log level semantics ("DEBUG: High-volume diagnostic detail", "INFO: Normal lifecycle events") but provides no guidance on per-item logging volume in high-throughput fetchers.

Scenario: The sync_epss_scores fetcher processes ~230,000 CVEs per daily run. If each CVE produces an INFO-level log line ("EPSS score updated for CVE-2024-XXXX"), a single fetcher run generates ~230,000 log lines in minutes. Multiply by the number of enrichment fetchers running daily and the log volume becomes significant. At LOG_LEVEL=INFO in production, these high-volume per-item logs dominate the stream, making it hard to find lifecycle events (run start/end, errors) without correlation ID filtering.

The spec delegates per-item logging decisions to individual feature specs ("It does not govern what individual services log at which level for their specific business logic"), which is correct in principle, but without a cross-cutting guideline, different fetcher specs will make inconsistent choices.

Suggested addition: a brief guideline in the log level table: "For batch operations processing >100 items, per-item success logs SHOULD use DEBUG; aggregate results (total created/updated/failed) SHOULD use INFO."

---

## Security

_(no findings — section is clean)_

---

## API Conventions

_(no findings — section is clean)_
