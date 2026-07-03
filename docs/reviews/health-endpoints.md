# Review: health-endpoints

**Spec**: `docs/features/platform/health-endpoints.md`
**Last reviewed**: 2026-07-03
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### HEP-GAP-01 — Redis failure condition incomplete (Medium)

**Category**: Error and failure paths
**Status**: OPEN

The Redis check failure condition in the spec states only "Connection refused or timeout" (the Checks performed table). However, Redis can fail in ways that are neither connection refusal nor timeout: (1) Redis in LOADING state returns `-LOADING Redis is loading the dataset in memory`; (2) Redis configured with `requirepass` returns `-NOAUTH Authentication required` if credentials are incorrect; (3) Redis may return other protocol-level errors. The PostgreSQL check column includes "or query error" as a catch-all, but the Redis column has no equivalent. An implementer must decide whether `NOAUTH` maps to `"unreachable"` (the connection succeeded, so it's not really unreachable) or requires a new status value. The fix is simple: add "or command error" to the Redis failure condition column, mirroring the PostgreSQL pattern.

### HEP-GAP-02 — Unhandled exception behavior unspecified (Medium)

**Category**: Error and failure paths
**Status**: OPEN

The spec states "This endpoint returns either 200 or 503 with the JSON body above. It never returns 4xx." but does not address what happens when the check code itself raises an unexpected exception (e.g., the asyncpg driver raises an unexpected error type, or a runtime error occurs during check execution). FastAPI's default exception handler would return a 500 with the standard `{"code": "INTERNAL_ERROR", ...}` body (per api-spec.md Global Responses), which has a different structure than the probe's `{"status": ..., "checks": {...}}` response. An orchestrator parsing the response body for the `checks` object would fail if it received a generic 500 error body. The spec should clarify whether ALL exceptions within the endpoint are caught and mapped to a 503 response (making the endpoint truly infallible from the orchestrator's perspective), or whether unexpected 500 responses are possible.

---

## Coherence

_No findings — the spec is fully aligned with architecture.md, deployment.md, api-spec.md, networking.md, and the RBAC Endpoint Permission Map._

---

## Design

### HEP-DES-01 — Sequential checks should be parallel (Medium)

**Category**: Complexity vs Simplicity
**Status**: OPEN

The spec states "Checks performed (sequentially, each with 2-second timeout)" resulting in a 4-second worst case. Since both checks are independent (no ordering dependency) and the implementation uses FastAPI (async), running them concurrently via `asyncio.gather` is trivial and halves worst-case latency from 4s to 2s. This matters operationally: the current orchestrator timeout recommendation of ">= 5s" leaves only 1s margin for HTTP overhead. With parallel execution, the recommendation becomes ">= 3s" with better operational margin. In scenarios where both dependencies are simultaneously slow (e.g., network partition), the 4s sequential response combined with any additional network latency (service mesh sidecars, cross-AZ routing) can cause the kubelet to time out before receiving the response, producing false-negative readiness failures.

### HEP-DES-02 — Redis connection target unspecified (Medium)

**Category**: Edge cases and ambiguity
**Status**: OPEN

The spec includes Redis in readiness because "without Redis, the API server cannot enqueue background tasks" but does not specify which Redis connection the PING targets. The deployment configuration defines multiple Redis URLs: `REDIS_URL` (session/cache on db 0), `CELERY_BROKER_URL` (task broker on db 1), `CELERY_RESULT_BACKEND` (results on db 2). In production, these could point to separate Redis instances. If the readiness check PINGs only `REDIS_URL`, the Celery broker could be down while the probe reports "ok" — contradicting the stated rationale (task enqueueing). The spec should explicitly state that the check targets `CELERY_BROKER_URL` (matching the rationale), or specify that it checks whichever Redis connection is relevant to API server functionality.

---

## Security

_No findings — the spec follows security best practices for health endpoints. Response bodies contain only generic component names, no sensitive data is exposed, and the unauthenticated design is appropriate for orchestrator probes._

---

## API Conventions

_No findings — all endpoint definitions conform to project API conventions. The intentional deviation from the `/api/v1/` envelope is properly documented and justified._
