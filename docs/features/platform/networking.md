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
def create_http_client(**overrides) -> httpx.AsyncClient:
    """Create a pre-configured httpx AsyncClient.

    Applies all cross-cutting defaults (User-Agent, timeouts, TLS,
    compression, Accept header, transport-level retry). Keyword
    arguments override individual defaults.
    """
```

### Default Configuration

| Setting | Default | Override mechanism |
|---------|---------|-------------------|
| User-Agent | `Sentinel/{version} ({name}; +https://github.com/SUSE/sentinel)` | Not overridable |
| Connect timeout | 10 seconds | `http_client_options` |
| Read timeout | 30 seconds | `http_client_options` |
| Write timeout | 10 seconds | `http_client_options` |
| Pool timeout | 10 seconds | `http_client_options` |
| Accept | `application/json` | `http_client_options` (headers) |
| Accept-Encoding | `gzip, deflate` (httpx built-in) | — |
| TLS | Combined trust store (system CAs + SUSE CA) | See "TLS Trust Store Configuration" section |
| Transport retry | See "Transport-Level Retry" below | `http_client_options` |
| Proxy | Standard env vars (`HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY`) | System-level |

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
| 5xx, connection error, timeout | 4 attempts (1 original + 3 retries) | 1s / 2s / 4s (fixed) |
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

**Shutdown**: all retry sleeps (both fixed-backoff and Retry-After waits)
use `asyncio.sleep()`, cancelled automatically on `SoftTimeLimitExceeded`
or task revocation. No special handling needed.

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
- Certificate rotation requires process restart (same as BaseFetcher)

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
  warning is emitted at startup (does not block startup)
- **If file is corrupt or unparseable**: SSL context creation raises an
  error at client creation time. The fetcher fails with a clear error
  message
- **TLS verification**: always enforced. Failed handshake is an immediate
  error — never proceed with an unverified connection
- **Certificate rotation**: SSL context is built at client creation time.
  Long-lived clients (IBSClient) require process restart to pick up a
  rotated CA certificate. Acceptable given CA rotations are infrequent
  (years between)

### Protocol-Specific Integration

| Protocol | Component | Trust Store Source |
|----------|-----------|-------------------|
| HTTPS | Shared HTTP client (all fetchers, IBSClient) | Combined trust store via factory |
| LDAPS | `sync_ldap_directory` fetcher | Same `SUSE_CA_CERT_PATH`, passed to python-ldap SSL context |
| AMQPS | `IBSEventConsumer` | Same `SUSE_CA_CERT_PATH`, passed to aio-pika/aiormq SSL context |

## Cross-references

- `docs/features/platform/fetcher-infrastructure.md` — BaseFetcher HTTP
  integration (lazy property, overrides)
- `docs/features/integrations/ibs-integration.md` — IBSClient usage
- `docs/features/identity/ad-integration.md` — LDAP TLS configuration
- `docs/features/integrations/ibs-rabbitmq-integration.md` — AMQP TLS
  configuration
- `docs/configuration.md` — environment variable index
