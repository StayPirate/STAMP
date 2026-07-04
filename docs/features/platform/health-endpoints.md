# Health Endpoints

## Purpose

Operational health probing endpoints for container orchestrators. These
endpoints allow Docker healthchecks and Kubernetes probes to determine
whether the Sentinel API server process is alive (liveness) and whether
it is ready to serve production traffic (readiness).

Both endpoints are registered at the application root (outside `/api/v1/`)
because they are infrastructure concerns, not domain API. They require no
authentication and do not follow the standard API response envelope.

## Endpoints

### Liveness — GET /health

```
GET /health
```

Confirms the FastAPI process is running and able to serve HTTP requests.
Does NOT verify downstream dependencies. If this endpoint fails to
respond, the orchestrator should restart the container.

**Response** (200 OK):

```json
{
  "status": "ok"
}
```

This endpoint has no failure path: if the process can respond to HTTP, it
returns 200. A non-response (connection refused, timeout) is the failure
signal to the orchestrator.

**`Access: Public`**

### Readiness — GET /ready

```
GET /ready
```

Verifies that the API server instance can serve production traffic by
checking connectivity to required runtime dependencies. If this endpoint
returns 503, the orchestrator should remove the instance from the load
balancer rotation but NOT restart it.

**Checks performed** (concurrently via `asyncio.gather`, each with
2-second timeout):

| Check | Operation | Failure condition |
|-------|-----------|-------------------|
| PostgreSQL | `SELECT 1` | Connection refused, timeout, or query error |
| Redis (per unique instance) | `PING` | Connection refused, timeout, or command error |

**Redis instance discovery**: the readiness check extracts `host:port`
from all three Redis configuration URLs (`REDIS_URL`,
`CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`), deduplicates by
`host:port`, and PINGs each unique instance in parallel. In the standard
single-instance deployment (all URLs point to the same host), this
results in a single PING. In split deployments (URLs pointing to
different Redis instances), each unique instance is verified
independently.

**Response** (200 OK — all checks pass):

```json
{
  "status": "ok",
  "checks": {
    "postgresql": "ok",
    "redis": "ok"
  }
}
```

When multiple unique Redis instances are discovered, the `redis` check
reports `"ok"` only if ALL instances respond successfully. If any
instance fails, the check reports the worst result (`"unreachable"` or
`"timeout"`).

**Response** (503 Service Unavailable — at least one check fails):

```json
{
  "status": "unavailable",
  "checks": {
    "postgresql": "ok",
    "redis": "unreachable"
  }
}
```

**Check result values**:

| Value | Meaning |
|-------|---------|
| `"ok"` | Check completed successfully |
| `"unreachable"` | Connection refused or other non-timeout error |
| `"timeout"` | No response within 2 seconds |

**`Access: Public`**

**Error responses**: None. This endpoint returns either 200 or 503 with
the JSON body above. It never returns 4xx. If any check raises an
unexpected exception (beyond the documented failure conditions), the
handler catches it, logs the exception at ERROR level, and reports that
check as `"unreachable"` in the response body, returning 503. This
endpoint never produces a 500 response — all exceptions are caught
internally, guaranteeing that orchestrators always receive the structured
probe response format.

## Orchestrator Configuration

The orchestrator probe configuration MUST account for the internal check
timeouts:

| Setting | Recommended value | Rationale |
|---------|-------------------|-----------|
| `timeoutSeconds` (K8s) / timeout (Docker) | >= 5s | Concurrent checks, each up to 2s = 2s worst case; >= 5s provides comfortable margin for network overhead (service mesh, cross-AZ routing) |
| `periodSeconds` / interval | 10-30s | Standard probe frequency |
| `failureThreshold` / retries | 3 | Avoid flapping on transient issues |

## Design Decisions

- **Path outside /api/v1/**: infrastructure endpoints are not part of the
  domain API. They do not use the standard response envelope (`"data"`
  wrapper), do not require authentication, and are not versioned.

- **No authentication**: orchestrator probes must call these without
  tokens. The response bodies do not expose sensitive information (no
  versions, hostnames, connection strings, or internal topology).

- **Redis included in readiness**: without Redis, the API server cannot
  enqueue background tasks. Operations that trigger tasks (ticket creation
  triggers CVE fetch, on-demand fetcher runs) appear to succeed but
  produce no follow-up processing. This constitutes a degraded state that
  the orchestrator should be aware of. The check discovers Redis instances
  dynamically from the configured URLs (`REDIS_URL`, `CELERY_BROKER_URL`,
  `CELERY_RESULT_BACKEND`) so that split deployments are automatically
  covered without spec or code changes.

- **SUSE CA certificate NOT included**: the CA is a dependency of Celery
  workers and the IBS RabbitMQ consumer, not of the API server process.
  The API server never connects to SUSE internal services directly.
  Including a worker prerequisite in the API readiness probe would create
  inappropriate coupling between independent processes. The fetcher
  dashboard already surfaces certificate-related failures with adequate
  visibility.

- **No "degraded" status**: the readiness probe is binary by design
  (ready / not ready). Kubernetes does not support a third state. If a
  future need arises for detailed system diagnostics, it should be a
  separate authenticated endpoint under `/api/v1/`.

- **No response caching**: each probe invocation performs fresh checks.
  The checks are lightweight (`SELECT 1`, `PING`) and the probe frequency
  is controlled by the orchestrator (typically 10-30s).

- **2-second timeout per check**: balances detecting genuinely slow
  services (not just unreachable ones) against keeping probe response time
  within orchestrator timeout limits. Checks run concurrently, so the
  worst-case endpoint response time is 2 seconds (the maximum of both
  checks), not the sum. Under normal operation, both checks complete in
  sub-millisecond time.

## Security Considerations

- Response bodies contain only generic component names (`"postgresql"`,
  `"redis"`) and status strings. No internal hostnames, ports, versions,
  or connection parameters are exposed.
- No rate limiting is applied — probe frequency is controlled by the
  orchestrator configuration, not by the application.
- These endpoints are excluded from authentication middleware entirely.

## Access Control

| Action | Access |
|--------|--------|
| GET /health | Public |
| GET /ready | Public |

## Cross-references

- `docs/architecture.md` — Health And Readiness (architectural intent)
- `docs/deployment.md` — Health Checks (operational configuration guide)
- `docs/features/platform/networking.md` — TLS Trust Store (SUSE CA
  deliberately excluded from readiness checks)
- `docs/api-spec.md` — General Conventions (explains why these endpoints
  do NOT follow the `/api/v1/` envelope format)
