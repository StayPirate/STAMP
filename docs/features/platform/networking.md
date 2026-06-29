# Networking Infrastructure

## Purpose

Cross-cutting HTTP client and TLS trust store infrastructure used by
all Sentinel components that make outgoing network connections: fetchers
(via BaseFetcher), IBSClient, `sync_ldap_directory` (LDAP), and
IBSEventConsumer (AMQP).

Scope boundary: this spec covers the shared HTTP client factory and TLS
trust store configuration. Protocol-level reconnection logic, connection
pooling, and application retry policies belong to their respective
integration specs.

## Shared HTTP Client

All outgoing HTTP requests from fetchers and service-level clients use
a shared HTTP client infrastructure based on httpx `AsyncClient`. The
infrastructure provides two layers:

1. **Standalone factory module** (`backend/app/services/http_client.py`):
   creates a pre-configured httpx `AsyncClient` with all cross-cutting
   defaults. Any component can call this factory — fetchers, `IBSClient`,
   or future consumers.

2. **BaseFetcher integration**: `BaseFetcher` exposes a `self.http_client`
   lazy property that internally calls the standalone factory. Fetcher
   authors use `self.http_client` directly — zero configuration, zero
   boilerplate. See `docs/features/platform/fetcher-infrastructure.md`
   (BaseFetcher HTTP Client Integration) for the lazy property, override
   mechanism, and lifecycle details.

### Factory Module

Location: `backend/app/services/http_client.py`

```python
def create_http_client(name: str, **overrides) -> httpx.AsyncClient:
    """Create a pre-configured httpx AsyncClient.

    Args:
        name: Identifies the calling component (fetcher name or client
              name). Used to build the User-Agent header and included
              in WARNING-level logs for TLS override traceability.

    Applies all cross-cutting defaults (User-Agent, timeouts, TLS,
    compression, Accept header, transport-level retry). Keyword
    arguments override individual defaults.
    """
```

#### Override Safety

Two settings receive special protection in the factory:

- **User-Agent**: always built from the standard template using the
  `name` parameter. Not overridable via `http_client_options` or
  `**overrides` — any `user-agent` key in headers or top-level
  `headers` override is silently dropped and replaced with the
  template-generated value.
- **TLS verify / ssl_context**: overridable via `**overrides` or
  `http_client_options`, but every override that sets `verify=False`
  or provides a custom `ssl_context` emits a WARNING-level log at
  client creation time, including the caller's `name` for
  traceability. Example log: `"TLS verify overridden by
  'sync_nvd_cves' — verify=False"`.

### Default Configuration

| Setting | Default | Override mechanism |
|---------|---------|-------------------|
| User-Agent | `Sentinel/{version} ({name}; +https://github.com/SUSE/sentinel)` | Not overridable |
| Connect timeout | 10 seconds | `http_client_options` |
| Read timeout | 30 seconds | `http_client_options` |
| Write timeout | 10 seconds | `http_client_options` |
| Pool timeout | 10 seconds | `http_client_options` |
| Max connections | 100 | `http_client_options` (limits) |
| Max keepalive connections | 20 | `http_client_options` (limits) |
| Accept | `application/json` | `http_client_options` (headers) |
| Accept-Encoding | `gzip, deflate` (httpx built-in) | — |
| TLS | Combined trust store (system CAs + SUSE CA), verify enabled | `http_client_options` / `**overrides` (emits WARNING — see "Override Safety") |
| Transport retry | See "Transport-Level Retry" below | `http_client_options` |
| Proxy | Standard env vars (`HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY`) | System-level |

Connection pool note: all current consumers make sequential requests
within a single task (one HTTP request at a time per client instance).
The pool exists for keepalive connection reuse between sequential
requests to the same host, not for managing parallelism. These limits
match httpx defaults and provide ample headroom for future consumers
with higher concurrency needs.

#### User-Agent

Format: `Sentinel/{version} ({fetcher.name}; +https://github.com/SUSE/sentinel)`

- Platform version from `importlib.metadata.version("sentinel")`. If
  `PackageNotFoundError` (running from source without installation),
  defaults to `"dev"`
- Fetcher name automatic from `BaseFetcher.name` (mandatory, unique)
- Project URL hardcoded — not configurable
- Example: `Sentinel/1.0 (sync_nvd_cves; +https://github.com/SUSE/sentinel)`
- Example (dev): `Sentinel/dev (sync_nvd_cves; +https://github.com/SUSE/sentinel)`

For non-fetcher components (e.g., `IBSClient`), the `name` parameter
is passed explicitly to the factory.

#### Timeouts

- Connect: 10 seconds (TCP + TLS handshake)
- Read: 30 seconds (time to receive response body)
- Write: 10 seconds (time to send request body)
- Pool: 10 seconds (time waiting for a connection from the pool)

Not configurable via env var or admin panel — these are engineering
decisions. Fetchers that need different values override via
`http_client_options`.

Timeout hierarchy (independent concerns):

```
┌─────────────────────────────────────────────────────┐
│ FetcherConfig.run_timeout (default: 3600s)          │  ← Celery task level
│ Detects stale runs (worker crashed, deadlock)       │     (per entire run)
│                                                     │
│  ┌───────────────────────────────────────────────┐  │
│  │ Per-HTTP-request timeout                      │  │  ← HTTP transport level
│  │ connect: 10s, read: 30s                       │  │     (per single request)
│  │                                               │  │
│  │ A single execute() run may make hundreds of   │  │
│  │ HTTP requests, each with its own timeout.     │  │
│  └───────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

#### Transport-Level Retry

The shared client automatically retries transient errors before the
fetcher sees them. If all retries fail, the error propagates to the
fetcher, which applies its own logic (abort, skip-and-continue, etc.).

**Dispatch rule**: when a response matches multiple rows (e.g., 503 is
both a 5xx and may carry `Retry-After`), the most specific row wins. If
`Retry-After` is present and parseable, the guided path is selected;
otherwise, the generic status-code row applies.

| Condition | Retry | Backoff |
|-----------|-------|---------|
| 5xx (any method) | 4 attempts (1 original + 3 retries) | 1s / 2s / 4s (fixed) |
| Connection error, timeout (idempotent methods only†) | 4 attempts (1 original + 3 retries) | 1s / 2s / 4s (fixed) |
| 429/503 with `Retry-After` ≤ 120s | 1 retry | Wait the indicated value |
| 429/503 with `Retry-After` > 120s | No retry | Error propagated immediately |
| 429 without `Retry-After` | No retry at transport | Fetcher decides |
| 4xx (non-429) | No retry | Client error — retrying is pointless |

**Path exclusivity**: if a response enters the Retry-After guided path,
the guided retry is the final attempt. The two retry paths are mutually
exclusive within a single request sequence. Consequence: a server that
sends `Retry-After` receives one guided retry, whereas the same status
without the header receives three fixed-backoff retries. This is
intentional — the server's explicit guidance replaces blind attempts.
If the server-guided retry fails, a more persistent issue is likely.

**Retry-After parsing**: integer (seconds) or HTTP-date (RFC 7231).
Malformed values (unparseable strings, negative integers) are treated as
absent — the response falls through to the "Retry-After absent" row.
For HTTP-date values, the wait is computed as `parsed_date - now()`; if
the result is ≤ 0 (date already elapsed or server clock ahead of client),
the value is treated as absent (same fallthrough). This prevents
zero-delay retries caused by server clock skew or stale dates and ensures
consistent behavior with the negative-integer rule.

**Shutdown**: all retry sleeps (both fixed-backoff and Retry-After waits)
use `asyncio.sleep()`, cancelled automatically on `SoftTimeLimitExceeded`
or task revocation. No special handling needed.

##### Method Safety

Per RFC 9110 Section 9.2.2, a client SHOULD NOT automatically retry a
request with a non-idempotent method unless it has means to know that
the request semantics are actually idempotent.

Default retry eligibility by method:

- **Idempotent methods** (GET, HEAD, OPTIONS, PUT, DELETE): retry on
  connection error, timeout (read/write), and 5xx with readable response
- **Non-idempotent methods** (POST, PATCH): retry on 5xx with readable
  response only. No retry on timeout or connection error by default —
  these conditions can occur after the server has received and begun
  processing the request, risking duplicate writes

The `†` marker in the retry condition table above indicates the
idempotent-method restriction.

**Opt-in for non-idempotent methods**: the factory accepts a
`retry_non_idempotent` parameter (boolean, default `False`). When
`True`, retry applies to all methods uniformly (connection error and
timeout are retried regardless of HTTP method). The caller is
responsible for ensuring their operations are semantically safe to
retry (e.g., IBS `cmd=diff` POST endpoints are read-only despite
using POST).

##### httpx Built-In Retry Exclusion

httpx's built-in transport retry (`retries` parameter on
`AsyncHTTPTransport`) is not used. The custom transport-level retry
described above replaces it entirely, providing unified backoff,
Retry-After support, method safety enforcement, and observability. Do
not enable `retries` on the transport — this would cause multiplicative
retry behavior (N httpx retries x M custom retries per attempt).

#### HTTP Response Compression

The HTTP client sends `Accept-Encoding: gzip, deflate` by default (httpx
built-in behavior using Python standard library codecs). Responses are
decompressed transparently. Brotli (`br`) additionally supported if the
`brotli` package is installed. No per-fetcher configuration needed.

#### Proxy Configuration

The shared HTTP client respects the standard `HTTPS_PROXY`, `HTTP_PROXY`,
and `NO_PROXY` environment variables for proxy configuration. No
application-level proxy settings exist. These are system-level variables
set at the container or host level.

If the deployment uses a TLS-intercepting proxy, the proxy's CA
certificate must be present in the system CA bundle (standard procedure,
no Sentinel-specific configuration needed).

### Non-Fetcher Components

`IBSClient` calls the standalone factory directly and manages its own
client lifecycle independently of `BaseFetcher`:

- Instantiated per-process (each Celery worker and IBSEventConsumer)
- Long-lived client with connection pooling
- Uses `Accept: application/xml` override (from JSON default)
- TLS validated via the same combined trust store
- httpx idle connection management (~5s timeout) prevents stale
  connections without manual intervention
- Certificate rotation requires process restart (long-lived client;
  see "Certificate rotation" below)

## TLS Trust Store Configuration

All outgoing TLS connections from Sentinel — HTTP (shared client), LDAP
(`sync_ldap_directory`), AMQP (`IBSEventConsumer`) — use a combined
trust store that includes both the system CA bundle and the SUSE
internal CA.

- **Env var**: `SUSE_CA_CERT_PATH` (default: `certs/SUSE_Trust_Root.crt`)
  - The SUSE Trust Root CA file is committed in the repository. The
    default path works both in containers (workdir `/app`) and in local
    development (run from project root). No configuration needed for
    standard deployments
  - The env var exists as an override for non-standard deployments
- **Combined trust store**: at runtime, Python builds an SSL context
  that includes system CAs (for public services: NVD, GitHub, CISA,
  Red Hat, OSV, FIRST.org) and the SUSE CA (for internal services: IBS,
  SMELT, AIMAAS, RabbitMQ). All connections use the same trust store —
  no host matching, no fallback, no host list to maintain
- **If file does not exist**: combined trust store contains only system
  CAs. Connections to SUSE internal services fail with TLS error. A log
  warning is emitted when `create_http_client()` is invoked (does not
  block startup). The file existence check runs inside
  `create_http_client()` when constructing the SSL context — the SSL
  context is built fresh on every invocation (no module-level caching).
  Warning frequency per component type:
  - Fetchers in batch mode (`execute()` loop): once per run — the client
    is created lazily on first access and reused for all `fetch_single()`
    calls within the same run
  - Standalone `fetch_single_cve` tasks: once per task — a client is
    created lazily and closed by the task wrapper per call
  - Standalone `run_catch_up` tasks: once per task — same pattern as
    `fetch_single_cve`
  - Long-lived clients (IBSClient, IBSEventConsumer): once per process
    lifetime
- **If file is corrupt or unparseable**: `build_tls_context()` raises
  `TLSConfigurationError` with the file path and parse error detail.
  The calling component handles this error according to its own
  lifecycle model (see each integration spec for component-specific
  behavior). This is a configuration error, not a transient condition
  — retrying without fixing the file is pointless
- **TLS verification**: enabled by default using the SUSE Trust Root CA
  bundle. Callers may override `verify` or provide a custom `ssl_context`
  via factory overrides; doing so causes a WARNING-level log at client
  creation (including the caller's `name`) to ensure visibility. Failed
  TLS handshake with verification enabled is an immediate error — never
  proceed with an unverified connection unless explicitly overridden
- **Certificate rotation**: since the SSL context is built fresh per
  `create_http_client()` invocation (not cached at module level):
  - Fetchers pick up a rotated CA automatically on the next run without
    process restart
  - IBSEventConsumer rebuilds its AMQPS SSL context on each
    reconnection attempt (see `ibs-rabbitmq-integration.md`), picking
    up a rotated CA automatically after any connection loss without
    process restart
  - IBSClient requires a process restart to pick up a rotated CA
    (long-lived HTTP client with no reconnection event that would
    trigger a rebuild)
  - Acceptable given CA rotations are infrequent (years between
    rotations)

### Shared Trust Store Function

All protocols use `build_tls_context()` to construct their SSL context:

```python
def build_tls_context() -> ssl.SSLContext:
    """Build the combined TLS trust store (system CAs + SUSE CA).

    Returns an ssl.SSLContext suitable for any protocol (HTTPS, LDAPS,
    AMQPS).

    Behavior:
    - SUSE_CA_CERT_PATH missing: log WARNING, return system-only context
    - SUSE_CA_CERT_PATH corrupt/unparseable: raise TLSConfigurationError
    - SUSE_CA_CERT_PATH valid: return combined context (system + SUSE CA)
    """
```

`create_http_client()` calls `build_tls_context()` internally.
Non-HTTP components (`sync_ldap_directory`, `IBSEventConsumer`) call it
directly and pass the returned context to their respective protocol
libraries.

Responsibility boundary: `build_tls_context()` is responsible only for
constructing the context and signaling errors. The *handling* of
`TLSConfigurationError` (retry, termination, failure marking) is owned
by each component and documented in its respective spec.

### Protocol-Specific Integration

| Protocol | Component | Trust Store Source |
|----------|-----------|-------------------|
| HTTPS | Shared HTTP client (all fetchers, IBSClient) | `build_tls_context()` via `create_http_client()` |
| LDAPS | `sync_ldap_directory` fetcher | `build_tls_context()` passed to python-ldap |
| AMQPS | `IBSEventConsumer` | `build_tls_context()` passed to aio-pika/aiormq |

## Cross-references

- `docs/features/platform/fetcher-infrastructure.md` — BaseFetcher HTTP
  integration (lazy property, overrides)
- `docs/features/integrations/ibs-integration.md` — IBSClient usage
- `docs/features/identity/ad-integration.md` — LDAP TLS configuration
- `docs/features/integrations/ibs-rabbitmq-integration.md` — AMQP TLS
  configuration
- `docs/configuration.md` — environment variable index
- RFC 9110 Section 9.2.2 — HTTP method idempotency semantics (normative
  basis for transport-level retry method safety)
