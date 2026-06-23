# HTTP Client Infrastructure for Fetchers

## Purpose

Evaluate and resolve open questions about how Sentinel fetchers make
outgoing HTTP requests. Today, each fetcher spec independently defines
(or omits) timeout values, retry behavior, request headers, and TLS
configuration. This draft collects all such concerns into a single
document so they can be discussed, resolved, and then applied to the
existing specifications in a controlled manner.

## Scope

This draft covers **outgoing HTTP requests from fetchers to external
services**. Additionally, OP-6 (TLS configuration) intentionally extends
beyond HTTP to cover all outgoing TLS connections (HTTP, LDAP, AMQP)
since all SUSE internal services share the same CA chain — the shared
trust store design applies uniformly across protocols.

This draft does NOT cover:

- LDAP connections (`sync_ldap_directory` — uses LDAP protocol, not HTTP;
  but OP-6 TLS trust store applies)
- AMQP connections (IBS RabbitMQ consumer — uses AMQP protocol; but OP-6
  TLS trust store applies)
- Git operations (MITRE, Kernel fetchers — use `git` subprocess)
- Incoming HTTP requests (FastAPI API layer)
- SSO/OIDC HTTP calls (covered by `sso-authentication.md`; the SSO module
  has its own timeout specifications independent of this draft)

## Fetcher Inventory

The following fetchers make outgoing HTTP requests and are affected by
decisions in this draft:

| # | Fetcher | Target service | Auth | Spec |
|---|---------|---------------|------|------|
| 1 | `sync_nvd_cves` | NVD REST API v2 (`services.nvd.nist.gov`) | API key (query param, optional) | `tickets/cve-sync-nvd.md` |
| 2 | `sync_redhat_cves` | Red Hat Security Data API (`access.redhat.com`) | None | `tickets/cve-sync-redhat.md` |
| 3 | `sync_ghsa_advisories` | GitHub Advisory REST API (`api.github.com`) | Bearer token | `tickets/cve-sync-ghsa.md` |
| 4 | `sync_osv_advisories` | OSV REST API (`api.osv.dev`) | None | `tickets/cve-sync-osv.md` |
| 5 | `sync_cisa_kev` | CISA KEV JSON feed (`www.cisa.gov`) | None | `tickets/cve-sync-kev.md` |
| 6 | `sync_epss_scores` | FIRST.org EPSS API (`api.first.org`) | None | `tickets/cve-sync-epss.md` (stub) |
| 7 | `sync_smelt_products` | SMELT (`smelt.suse.de/api`) | TBD | `packages/product-catalog.md` |
| 8 | `sync_aimaas_lifecycle` | AIMAAS (`aimaas.suse.de/api`) | TBD | `packages/product-catalog.md` |
| 9 | `sync_aimaas_thresholds` | AIMAAS (`aimaas.suse.de/api`) | TBD | `packages/product-catalog.md` |
| 10 | `detect_ibs_track_releases` | IBS API (`api.suse.de`) | HTTP Basic Auth | `packages/ibs-track-release-detection.md` |
| 11 | `detect_ibs_product_releases` | IBS download (`download.suse.de/ibs`) | HTTP Basic Auth | `packages/ibs-product-release-detection.md` |
| 12 | `sync_ibs_bugowners` | IBS API (`api.suse.de`) | HTTP Basic Auth | `packages/package-bugowner.md` |
| 13 | `sync_ibs_requests` | IBS API (`api.suse.de`) | HTTP Basic Auth | `packages/ibs-submission-tracking.md` |

Additionally, these components make HTTP requests but are NOT
`BaseFetcher` subclasses:

| Component | Target | Auth | Spec |
|-----------|--------|------|------|
| `IBSClient` | IBS API (`api.suse.de`) | HTTP Basic Auth | `integrations/ibs-integration.md` |
| `IBSEventConsumer` | IBS API (via `IBSClient`) | HTTP Basic Auth | `integrations/ibs-rabbitmq-integration.md` |
| `create_ticket_from_detection` | NVD API (via `fetch_single`) | API key | `integrations/ibs-integration.md` |

---

## HTTP Client Defaults (Quick-Resolution Group)

All open points in this group have been resolved. They had answers
largely dictated by standard HTTP best practices and were reviewed
and confirmed as a batch.

---

### OP-1: User-Agent header

**Problem**: no fetcher spec defines a `User-Agent` header for outgoing
HTTP requests. Without one, requests are sent with the default
User-Agent of whatever HTTP library is chosen (e.g.,
`python-httpx/0.28.x` or `python-requests/2.32.x`). External service
operators cannot identify Sentinel as the caller.

**Current state in specs**: only the unapproved EPSS draft
(`docs/drafts/epss-fetcher-workplan.md`, OP-12) proposes
`Sentinel/1.0 (EPSS fetcher)`. No approved spec addresses this.

**Options**:

**A) Single global User-Agent for all fetchers**

Format: `Sentinel/{version}`

- Pro: maximum simplicity — one value, one place
- Con: no way for an external provider to distinguish which fetcher is
  generating traffic

**B) Per-fetcher User-Agent (manually configured)**

Each fetcher spec defines its own User-Agent string.

- Pro: full granularity
- Con: manual effort, risk of inconsistency, boilerplate in every spec

**C) Composed User-Agent (base + fetcher name + project URL, automatic)**

Format: `Sentinel/{version} ({fetcher.name}; +https://github.com/SUSE/sentinel)`

The base string is centralized. The fetcher name comes from the
`name` attribute already required by the `BaseFetcher` contract.
The project URL is hardcoded — it identifies the project and
provides a contact path (issue tracker) for API providers.
Composition is automatic — fetcher authors do nothing.

- Pro: combines global identity with per-fetcher granularity at zero
  effort for authors; the `name` attribute already exists and is
  unique per fetcher
- Pro: API providers can identify the project and reach operators
  via the GitHub issue tracker
- Con: reveals internal fetcher names to external providers (minimal
  risk — names are generic and descriptive)

**D) No dedicated User-Agent (library default)**

- Pro: zero effort
- Con: no identification; NVD recommends identifying applications
  for favorable rate limits; unprofessional for enterprise software

**Recommendation**: Option C.

**Status**: `resolved`

**Resolution**: Option C — composed User-Agent with project URL.

- Format: `Sentinel/{version} ({fetcher.name}; +https://github.com/SUSE/sentinel)`
- Example: `Sentinel/1.0 (sync_nvd_cves; +https://github.com/SUSE/sentinel)`
- Platform version from the canonical source (e.g., `pyproject.toml`
  via `importlib.metadata`) — no duplication. If
  `importlib.metadata.version()` raises `PackageNotFoundError` (e.g.,
  running from source without installation), the version component
  defaults to `"dev"`.
  Example: `Sentinel/dev (sync_nvd_cves; +https://github.com/SUSE/sentinel)`
- No per-fetcher versioning — fetchers do not have independent
  release cycles; the platform version captures all changes
- Project URL hardcoded in the code — not configurable via env var.
  It is the project's identity, not a per-deployment setting
- The fetcher name is automatic from `BaseFetcher.name` (already
  mandatory and unique). It provides value for internal services
  where multiple fetchers hit the same host (e.g., IBS has 5
  consumers on `api.suse.de`)
- Collateral action: add a brief "For API Providers" section in the
  project README directing providers to open issues for traffic
  concerns

---

### OP-5: HTTP response compression (Accept-Encoding)

**Problem**: no fetcher spec mentions HTTP response compression. Some
fetchers download substantial payloads (NVD pages up to 2000 CVEs,
KEV full catalog, IBS `updateinfo.xml` files). Enabling compression
could significantly reduce bandwidth and transfer time.

**Current state in specs**: no mention of `Accept-Encoding`, `gzip`,
or `deflate` in any fetcher spec. The only compression reference is
in `ibs-rabbitmq-integration.md:29` regarding `updateinfo.xml`
decompression (different context — that is gzip-compressed at the
repository level, not HTTP-level compression).

**Options**:

**A) Enable by default in shared client**

The shared HTTP client sends `Accept-Encoding: gzip, deflate` by
default. Most modern HTTP libraries do this automatically when the
appropriate decompression library is available. The spec documents
this as default behavior.

- Pro: bandwidth reduction for free; transparent to fetcher code
- Con: negligible — decompression CPU cost is trivial

**B) Don't specify — leave to HTTP library defaults**

- Pro: no spec changes needed
- Con: behavior depends on the library chosen and its configuration;
  not explicitly documented

**Recommendation**: Option A.

**Status**: `resolved`

**Resolution**: Option A — document compression as a default behavior
of the shared HTTP client.

- httpx sends `Accept-Encoding: gzip, deflate` by default using
  Python standard library codecs (always available — no system
  packages or additional Python dependencies required)
- Brotli (`br`) is additionally supported if the `brotli` Python
  package is installed, but it is not a required dependency
- Responses are decompressed transparently by the HTTP library
- No per-fetcher configuration needed
- If httpx adds support for new compression codecs in future
  versions (e.g., zstd), they are picked up automatically with no
  code changes
- This is purely a documentation concern — no implementation code
  is needed to enable it

---

### OP-7: Default Accept header

**Problem**: only the GHSA fetcher specifies an `Accept` header
(`application/vnd.github+json`, required by GitHub's API). No other
fetcher spec declares what content type it expects. While most APIs
return JSON by default, relying on server defaults is fragile.

**Current state in specs**:

| Fetcher | Accept header | Response format |
|---------|---------------|-----------------|
| `sync_ghsa_advisories` | `application/vnd.github+json` | JSON |
| `sync_nvd_cves` | Not specified | JSON |
| `sync_redhat_cves` | Not specified | JSON |
| `sync_osv_advisories` | Not specified | JSON |
| `sync_cisa_kev` | Not specified | JSON |
| `sync_epss_scores` | Not specified | JSON (API) |
| IBS fetchers (via `IBSClient`) | Not specified | XML |
| SMELT/AIMAAS fetchers | Not specified | JSON |

**Options**:

**A) Default Accept in shared client, per-fetcher override**

The shared client sets `Accept: application/json` by default. XML
consumers (IBS) override to `application/xml`. Service-specific
Accept values (GitHub) override as they already do.

- Pro: explicit content negotiation; fails early if a server returns
  an unexpected format
- Con: minor — adds a header that most servers ignore anyway

**B) No default — per-fetcher only when required by the API**

Only specify `Accept` when the target API requires it (as GHSA does).

- Pro: minimal spec changes
- Con: inconsistent; not all fetchers negotiate content type

**Recommendation**: Option A.

**Status**: `resolved`

**Resolution**: Option A — `Accept: application/json` as the default
header in the shared HTTP client.

- 10 of 13 HTTP fetchers consume JSON — the default covers the
  majority with zero per-fetcher effort
- Non-JSON consumers override explicitly:
  - IBSClient → `Accept: application/xml`
  - GHSA → `Accept: application/vnd.github+json` (already specified)
- Override mechanism: at client instantiation level or per-request
  (both supported by httpx)
- Whether IBS fetchers override individually or inherit from
  IBSClient depends on the IBSClient design (OP-2 territory)
- No preemptive verification of all sources needed — if a server
  ignores the Accept header, it responds in its default format
  regardless (HTTP content negotiation is non-binding)

---

### OP-8: Conditional HTTP requests (ETag / If-Modified-Since)

**Problem**: some fetchers download the same content repeatedly
across runs. HTTP conditional requests (`If-None-Match` with ETag,
`If-Modified-Since`) can avoid redundant downloads when the content
has not changed.

**Current state in specs**:
- `ibs-rabbitmq-integration.md:29-31`: evaluated and explicitly
  rejected for IBS repo.published events ("the benefit does not
  justify the complexity")
- `ibs-product-release-detection.md:288-290`: listed as an open item
  ("Strategy for caching repomd.xml / updateinfo.xml / primary.xml
  (ETag, Last-Modified)")
- EPSS draft (`epss-fetcher-workplan.md:85`): notes that FIRST.org
  serves `Last-Modified` on daily CSV files

**Applicability assessment**:

| Fetcher | Benefit | Rationale |
|---------|---------|-----------|
| `sync_cisa_kev` | Medium | Single JSON file (~1MB); re-downloaded 4x/day. 304 saves bandwidth when catalog hasn't changed |
| `detect_ibs_product_releases` | High | Downloads `updateinfo.xml` files (can be large) for many product repositories |
| `sync_epss_scores` | Medium | Daily CSV download; no benefit if file changes daily |
| `sync_nvd_cves` | Low | Cursor-based incremental; only fetches modified CVEs |
| `sync_redhat_cves` | None | Per-CVE API calls; no repeated bulk download |
| `sync_ghsa_advisories` | None | Cursor-based incremental |
| IBS API fetchers | None | Dynamic API responses, not cacheable |

**Options**:

**A) Shared utility for conditional requests, opt-in per fetcher**

The shared client (OP-2) provides utility methods to store and reuse
`ETag`/`Last-Modified` values from previous responses (e.g., in the
database or in the fetcher's cursor). Fetchers opt in to conditional
requests when beneficial.

- Pro: infrastructure available when needed; no fetcher forced to
  use it
- Con: adds complexity to the shared client; requires storage for
  ETag/Last-Modified values

**B) Per-fetcher implementation when beneficial**

Each fetcher that benefits from conditional requests implements
its own caching. No shared infrastructure.

- Pro: simpler shared client
- Con: duplicated logic across fetchers that use it

**C) Defer entirely — not needed for v1**

None of the current fetcher volumes justify the optimization.
Address when/if a fetcher demonstrates a concrete performance
problem.

- Pro: no work now
- Con: missed optimization for IBS product release detection
  (already flagged as an open item)

**Recommendation**: Option C for v1.

**Status**: `resolved`

**Resolution**: Option C — defer entirely for v1.

- No fetcher currently demonstrates a performance problem caused
  by redundant downloads
- The only high-benefit case (IBS product release detection) already
  tracks this as its own spec-level open item
  (`ibs-product-release-detection.md:288-290`) — the solution will
  be designed in that context where the specific requirements
  (storage mechanism, invalidation strategy, cache key design,
  per-fetcher 304 semantics) are clearer
- The shared client (OP-2) must not preclude future conditional
  request support (i.e., do not strip ETag/Last-Modified from
  responses), but must not implement storage or opt-in mechanisms
  now
- When the IBS case is resolved, it may inform a reusable pattern
  for other fetchers — but that is speculative today

---

### OP-9: Retry-After header handling

**Problem**: the current spec explicitly ignores the `Retry-After`
header in 429 responses (`fetcher-infrastructure.md:433-434`,
`cve-sync-nvd.md:225`). This is a deliberate v1 simplification, but
it means fetchers may waste retry attempts when the rate-limit window
exceeds the fixed backoff total (35s).

**Current state in specs**: two specs document the decision:
- `fetcher-infrastructure.md:433-434`: "This design deliberately
  ignores the Retry-After header for simplicity."
- `cve-sync-nvd.md:225`: "The Retry-After header is deliberately
  ignored for simplicity"

Both note that the 35s total backoff naturally clears most rate-limit
windows (NVD: 30s window).

**Options**:

**A) Respect Retry-After in shared client (transport-level)**

If OP-4 adds transport-level retry, the retry mechanism reads
`Retry-After` from 429/503 responses and waits accordingly (with a
reasonable cap, e.g., 300s).

- Pro: optimal behavior — waits exactly as long as the server
  requests
- Pro: prevents wasted retry attempts
- Con: adds complexity to the retry mechanism; a long `Retry-After`
  (e.g., 3600s) could block a worker (mitigated by cap)

**B) Keep current approach — ignore Retry-After**

The fixed backoff (5s/10s/20s) remains. Documented as a v1
trade-off.

- Pro: simplicity
- Con: may fail on providers with rate-limit windows > 35s

**C) Opt-in per fetcher**

Individual fetchers choose whether to respect `Retry-After`. The
shared client provides the capability but doesn't enable it by
default.

- Pro: flexibility per provider
- Con: more spec work per fetcher

**Recommendation**: Option A (revised from original Option B).

The original recommendation was to ignore Retry-After for simplicity.
After review, the complexity of respecting it is minimal (~15 lines
of logic), and fixed backoff is a blind guess that fails when the
server's rate-limit window exceeds 35s (e.g., NVD without API key:
60s window). Respecting the server's explicit signal is more correct
HTTP behavior with trivial implementation cost.

**Status**: `resolved`

**Resolution**: Option A — respect Retry-After with a safety cap.

Transport-level behavior for 429 and 503 responses:

| Condition | Behavior |
|-----------|----------|
| `Retry-After` present, value ≤ 120s | Wait the indicated value, retry once |
| `Retry-After` present, value > 120s | Do not retry — propagate error to caller |
| `Retry-After` absent (429) | Do not retry at transport level — the fetcher decides |
| `Retry-After` absent (503) | Fixed backoff (same as other 5xx, per OP-4) |

Design details:

- **Cap**: 120 seconds. A worker sleeping 60s is acceptable; 120s
  is the upper bound of reasonable. Beyond that, the rate-limit is
  too aggressive for inline retry — fail and let the next scheduled
  run handle it
- **One retry only**: if the server returns 429 again after the
  guided wait, do not retry further — propagate the error. This
  prevents loops with servers that send escalating Retry-After values
- **Parsing**: `Retry-After` is either an integer (seconds) or an
  HTTP-date (RFC 7231). Most APIs use the integer format. The
  parsing is trivial
- **Malformed values**: unparseable strings, negative integers, or
  otherwise invalid `Retry-After` values are treated as absent — the
  response falls through to the "Retry-After absent" row in the
  policy table
- **Impact on OP-4**: this decision modifies the OP-4
  recommendation. 429 with `Retry-After` is now handled at transport
  level (previously excluded entirely). 429 without `Retry-After`
  remains excluded. See OP-4 for the updated retry scope

- **Shutdown interaction**: the Retry-After sleep uses
  `asyncio.sleep()`, which is cancelled when Celery raises
  `SoftTimeLimitExceeded` or the task is revoked. No special handling
  is needed — standard asyncio cancellation ensures timely shutdown

Cleanup required in existing specs:

- `fetcher-infrastructure.md:433-434`: remove the "Retry-After is
  deliberately ignored for simplicity" note; document the new
  transport behavior
- `cve-sync-nvd.md:225`: remove the corresponding note
- `cve-sync-nvd.md`: remove inline 429 retry logic (3x with
  5s/10s/20s) — it becomes redundant (transport handles the common
  case) and inconsistent (uses fixed backoff instead of
  Retry-After). Replace with: "if 429 persists after transport
  retry, abort run (`status = failure`); cursor does not advance —
   the next scheduled run retries the same time window"
- `ibs-integration.md`: remove "IBS API calls use retry logic with
  exponential backoff" (line 241) — superseded by transport-level
  retry. See OP-4 resolution (IBSClient retry resolution)

---

## Architectural Decisions (Discussion Required)

These open points require genuine architectural discussion because
the answers are not obvious, have significant design implications, or
involve trade-offs that depend on judgment about the system's future
evolution.

---

### OP-2: Shared HTTP client factory in BaseFetcher

**Problem**: `BaseFetcher` provides run lifecycle management, metric
helpers, and custom settings, but no assistance for HTTP requests.
Each of the 13 HTTP-based fetchers must independently create and
configure an HTTP client, leading to:
- Duplicated configuration (timeout, retry, User-Agent, TLS)
- Risk of inconsistency between fetchers
- No single place to enforce cross-cutting policies

**Current state in specs**: the `fetcher-infrastructure.md` code
example at line 903 uses `self.http_client.get(IBS_API_URL)`,
suggesting an HTTP client on the fetcher instance, but this is inside
an illustrative error-handling example — it is not part of the
`BaseFetcher` contract. The EPSS draft (OP-12) explicitly asks:
"identify shared HTTP client infrastructure that already handles
these."

`IBSClient` (`ibs-integration.md:177-206`) is a service-specific
client, not a general-purpose factory.

**Options**:

**A) BaseFetcher provides a pre-configured HTTP client**

`BaseFetcher` offers a method (e.g., `self.create_http_client()`) or
a property (e.g., `self.http_client`) that returns an HTTP client
pre-configured with:
- User-Agent (from OP-1)
- Default timeouts (from OP-3)
- TLS configuration for SUSE internal services (from OP-6)

Fetchers can override defaults or create their own client if needed.

- Pro: consistency enforced by default; fetcher authors get a working
  client with no configuration
- Pro: single place to change cross-cutting behavior
- Con: adds a responsibility to `BaseFetcher` that is currently absent

**B) Standalone HTTP client factory (separate module)**

A shared utility module (e.g., `http_client.py`) provides a factory
function that any component can use. Not tied to `BaseFetcher`.

- Pro: usable by non-fetcher components (e.g., `IBSClient`, SSO)
- Pro: keeps `BaseFetcher` focused on run lifecycle
- Con: fetcher authors must explicitly call the factory — no
  automatic integration

**C) No shared client — each fetcher manages its own**

- Pro: maximum flexibility per fetcher
- Con: guaranteed inconsistency; every fetcher repeats the same
  configuration; cross-cutting changes require modifying every spec

**Recommendation**: Option A, with BaseFetcher internally using a
shared factory (Option B) so that non-fetcher components like
`IBSClient` can also benefit. This gives fetchers zero-effort
defaults while keeping the factory reusable.

#### Client Lifecycle Design Questions

The choice of client lifecycle pattern has significant performance
and correctness implications. httpx `AsyncClient` supports connection
pooling — a persistent client instance reuses TCP connections across
requests, which matters for fetchers that make many sequential
requests to the same host.

**Performance impact of connection pooling**:

| Fetcher | Requests per run | Benefit of pooling |
|---------|------------------|--------------------|
| `sync_nvd_cves` | Hundreds (paginated) | High — same host, sequential |
| `sync_redhat_cves` | Thousands (per-CVE) | High — same host, sequential |
| `sync_osv_advisories` | Hundreds (per-alias) | High — same host, sequential |
| `sync_ghsa_advisories` | Tens to hundreds (paginated) | Medium |
| IBS fetchers | Tens to hundreds | Medium — same host |
| `sync_cisa_kev` | 1 | None |

**Lifecycle options to evaluate**:

1. **Lazy property** (`self.http_client`): client created on first
   access, reused for the entire `execute()` run, closed at run end.
   Connection pooling active for the duration of the run.
   - Pro: zero ceremony for fetcher authors; automatic pooling
   - Con: BaseFetcher must manage client teardown (in `run()` finally
     block or similar)

2. **Factory method** (`self.create_http_client()`): returns a new
   client instance on each call. Fetcher manages lifecycle with
   `async with`.
   - Pro: explicit lifecycle control; no state on BaseFetcher
   - Con: fetcher authors must wrap usage in context manager;
     forgetting to close leaks connections

3. **Async context manager** (`async with self.http_client() as client`):
   BaseFetcher provides a context manager that yields a pre-configured
   client.
   - Pro: explicit lifecycle with guaranteed cleanup
   - Con: slightly more ceremony than a property; nesting in
     `execute()` adds indentation

**IBSClient relationship**: `IBSClient` has a different lifecycle — it
is a service-level object shared between the `IBSEventConsumer`
(long-running) and periodic fetchers. If the shared factory (Option B
component) is the internal building block, `IBSClient` can use it to
create its own long-lived client independently of BaseFetcher's
per-run lifecycle.

**Status**: `resolved`

**Resolution**: A+B hybrid with lazy property lifecycle.

**Architecture** (two layers):

1. **Standalone factory module** (`backend/app/services/http_client.py`):
   a function that creates a pre-configured httpx `AsyncClient` with all
   cross-cutting defaults (User-Agent per OP-1, timeouts per OP-3,
   compression per OP-5, Accept header per OP-7, transport-level retry
   per OP-4). Any component in the system can call this factory —
   fetchers, `IBSClient`, or future consumers.

2. **BaseFetcher integration**: `BaseFetcher` exposes a `self.http_client`
   lazy property that internally calls the standalone factory. The client
   is created on first access during `execute()` and closed automatically
   by `BaseFetcher.run()` in its `finally` block. Fetcher authors use
   `self.http_client` directly — zero configuration, zero boilerplate.

**Lifecycle — lazy property pattern**:

```python
class MyFetcher(BaseFetcher):
    async def execute(self):
        # Client created on first access, pooling active for the
        # entire run, closed automatically when execute() ends.
        response = await self.http_client.get("https://example.com/api")
```

- Client created lazily on first `self.http_client` access
- Lives for the duration of a single `execute()` run
- Connection pooling active throughout the run (benefits fetchers
  that make many sequential requests to the same host: NVD, Red Hat,
  OSV, IBS)
- Destroyed by `BaseFetcher.run()` in the `finally` block (same
  place that calls `record_end`)
- Between runs (hours apart), no client exists — no idle connections

**Stale connection handling**: httpx closes idle connections after ~5
seconds of inactivity within the pool. If a server closes a connection
before that (shorter keep-alive), httpx detects the stale connection on
the next request attempt and transparently opens a new one. Given that
inter-request delays in fetchers are typically 0.2s–6s (rate limiting),
connections are reused effectively within a run. No special
configuration is needed.

**Override mechanism**: fetchers with non-standard requirements (e.g.,
longer read timeout for large downloads) can override the defaults by
passing keyword arguments:

```python
class ProductReleaseFetcher(BaseFetcher):
    # Override default read timeout for large XML downloads
    http_client_options = {"timeout": httpx.Timeout(10.0, read=120.0)}
```

The exact override API (class attribute, method parameter, or both)
will be defined in the implementation. The principle is: defaults work
for the majority; overrides are explicit and visible.

**Merge semantics**: `http_client_options` entries are passed as keyword
arguments to the factory, overriding individual defaults. Headers are
merged: for same-key headers, the fetcher-specific value replaces the
factory default (last-writer-wins). User-Agent is the sole exception —
it is always preserved and cannot be overridden. Other options (timeout,
transport configuration) replace the corresponding default at the
top-level kwarg level — they are not deep-merged.

**IBSClient relationship**: `IBSClient` is a service-level object with
a different lifecycle — it is instantiated per-process (each Celery
worker and the `IBSEventConsumer` process has its own instance). The
factory call and configuration are shared (same code path, same
defaults), but instances are independent across processes. It calls the
standalone factory directly (layer 1) and manages its own client
lifecycle independently of `BaseFetcher`. httpx's internal keep-alive
management (~5s idle timeout) applies identically to long-lived clients,
preventing stale connections without manual intervention.

**`fetch_single()` and `catch_up()` HTTP client lifecycle**:

`fetch_single()` is called in three contexts with different lifecycle
characteristics:

| Context | Inside `run()`? | `self.http_client` pre-existing? |
|---------|-----------------|----------------------------------|
| Inside `execute()` loop (Pattern B: Red Hat, OSV) | Yes | Yes — `run()` manages lifecycle |
| From `fetch_single_cve` Celery task orchestrator | No | No — standalone invocation |
| From `catch_up()` via `run_catch_up` Celery task | No | No — standalone invocation |

To make `fetch_single()` safe to call from **any** context without
external lifecycle management, the following contract applies:

- If `self._http_client` already exists (the lazy property was accessed
  during an active `run()` → `execute()` flow), `fetch_single()` **reuses
  it**. Connection pooling is preserved for Pattern B fetchers (Red Hat,
  OSV) that call `fetch_single()` in a loop within `execute()`
- If `self._http_client` does NOT exist (standalone invocation from a task
  wrapper, a test, a script, or any future call site), `fetch_single()`
  **creates a temporary client for the duration of the call and closes it
  automatically on return**
- `catch_up()` inherits this behavior automatically — it calls
  `self.fetch_single()` internally
- **No caller responsibility**: task wrappers (`fetch_single_cve`,
  `run_catch_up`) do not need to manage HTTP client lifecycle. The fetcher
  is self-sufficient
- **Error handling**: if the temporary client creation fails (e.g., TLS
  misconfiguration), the exception propagates normally to the caller. No
  cleanup is needed for a client that was never created

This design ensures that:
1. `fetch_single()` is safe to call from anywhere — no resource leaks
2. Pattern B performance is preserved (pooling within `execute()`)
3. Future code paths (direct API calls, new task types) work without
   special lifecycle ceremony

**Teardown safety**: if the lazy property was never accessed during a
`run()` invocation (e.g., `execute()` raised before its first HTTP
request), the teardown in `run()`'s `finally` block is a no-op.

---

### OP-3: Default HTTP request timeout

**Problem**: most fetcher specs mention "timeout" only in error
handling tables without specifying an actual value for HTTP request
timeouts. A fetcher without a timeout can block a Celery worker
indefinitely waiting for a response from an unresponsive service.

**Current state in specs**:

| Fetcher / Component | Timeout specified | Value | Location |
|---------------------|-------------------|-------|----------|
| `sync_cisa_kev` | Yes | 30s | `cve-sync-kev.md:68` |
| SSO (discovery, JWKS) | Yes | 5s | `sso-authentication.md:98` |
| SSO (token exchange) | Yes | 10s | `sso-authentication.md:230` |
| `sync_ldap_directory` | Yes (LDAP, not HTTP) | 30s connect / 120s op | `ad-integration.md:112-113` |
| `FetcherConfig.timeout_seconds` | Yes (task-level) | 3600s default | `fetcher-infrastructure.md:2688` |
| All other HTTP fetchers | No | — | — |

Note: `FetcherConfig.timeout_seconds` is a Celery task-level timeout
(stale run detection), NOT an HTTP request timeout. These are
independent concerns.

**Options**:

**A) Default timeout in shared client, per-fetcher override**

The shared client (OP-2) applies a default timeout (e.g., 30s
connect, 60s read). Individual fetchers override when needed (e.g.,
product release detection downloads large XML files and may need
longer read timeouts).

- Pro: safe default for all fetchers; no fetcher silently runs without
  a timeout
- Pro: explicit overrides are visible and documented
- Con: choosing the default values requires judgment

**B) Mandatory per-fetcher timeout in properties table**

Each fetcher spec MUST declare its HTTP timeout in the properties
table (like KEV already does). No default fallback.

- Pro: every fetcher has an explicitly chosen value
- Con: boilerplate; most fetchers would use the same value; risk of
  omission in new fetcher specs

**C) No default, optional per-fetcher**

Status quo. Fetchers specify timeouts only when they choose to.

- Pro: no cross-cutting changes needed
- Con: fetchers without explicit timeouts are vulnerable to indefinite
  blocking

**Recommendation**: Option A.

#### Proposed Default Values

Starting point for discussion:

| Timeout type | Proposed default | Rationale |
|--------------|-----------------|-----------|
| Connect timeout | **10 seconds** | Time to establish TCP + TLS handshake. 10s is generous for both public APIs (NVD, GitHub) and SUSE internal services. A server that doesn't accept a connection in 10s is likely down |
| Read timeout | **30 seconds** | Time to receive the response body after the request is sent. 30s covers most API responses (JSON payloads from NVD, Red Hat, GHSA are typically <1s). Matches the only existing explicit timeout (KEV: 30s) |

**Fetchers expected to override**:

| Fetcher | Override needed | Reason |
|---------|---------------|--------|
| `detect_ibs_product_releases` | Read timeout → 120s | Downloads `updateinfo.xml` files that can be several MB for large product repositories |

**Timeout hierarchy (clarification)**:

```
┌─────────────────────────────────────────────────────┐
│ FetcherConfig.run_timeout (default: 3600s)          │  ← Celery task level
│ Detects stale runs (worker crashed, deadlock)       │     (stale run detection)
│                                                     │
│  ┌───────────────────────────────────────────────┐  │
│  │ Per-HTTP-request timeout (this OP)            │  │  ← HTTP transport level
│  │ connect: 10s, read: 30s (defaults)            │  │     (per request)
│  │                                               │  │
│  │ A single execute() run may make hundreds of   │  │
│  │ HTTP requests, each with its own timeout.     │  │
│  └───────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

**Status**: `resolved`

**Resolution**: Option A — default timeout in shared client, per-fetcher
code-level override.

- **Default values** (hardcoded in the standalone factory from OP-2):
  - Connect timeout: 10 seconds
  - Read timeout: 30 seconds
- **Configurability**: none. No env var, no `FetcherConfig` field, no
  admin panel. HTTP request timeouts are engineering decisions, not
  operational knobs — they are almost never tuned at runtime. If a
  fetcher consistently hits the timeout, the correct fix is a code-level
  override (a conscious engineering decision), not a runtime knob
- **Override mechanism**: per-fetcher via `http_client_options` class
  attribute (same mechanism defined in OP-2). Only fetchers that
  genuinely need different values declare an override. Currently only
  `detect_ibs_product_releases` (read → 120s for large XML downloads)
- **Alignment**: `sync_cisa_kev` currently declares an explicit 30s
  timeout in its spec — this is now redundant (matches the default) and
  should be removed when applying this draft to specs
- **Not overriding**: `sync_epss_scores` (uses API calls, not bulk
  download) and `sync_nvd_cves` (only fetches recent CVEs, not
  historical bulk — pages are small) do not need overrides

---

### OP-4: HTTP-level retry for transient errors

**Problem**: the retry policy for `fetch_single` is well-defined at
the Celery task level (`fetcher-infrastructure.md:365-437`): 3x with
5s/10s/20s backoff for network errors, 5xx, timeout, 429. But inside
`execute()`, where a fetcher makes many HTTP requests (NVD paginates
through hundreds of pages, Red Hat iterates over thousands of CVEs),
transient failures on individual requests are handled differently by
each spec.

**Current state in specs**:

| Fetcher | execute() retry for individual requests |
|---------|-----------------------------------------|
| `sync_nvd_cves` | Inline retry: 3x with 5s/10s/20s for 429 only (`cve-sync-nvd.md:213-227`) |
| `sync_ghsa_advisories` | No retry within execute() — page failure aborts the run (`cve-sync-ghsa.md:501-505`) |
| `sync_redhat_cves` | 429/5xx: `record_failed`, continue to next CVE (`cve-sync-redhat.md:309-311,344`) |
| `sync_osv_advisories` | Per-alias: 5xx/timeout → skip with `record_failed` (`cve-sync-osv.md:103,380`) |
| `sync_cisa_kev` | Single-request fetcher; failure aborts (`cve-sync-kev.md:163`) |
| IBS fetchers | "retry logic with exponential backoff" (generic, `ibs-integration.md:241`) |

**Options**:

**A) Transport-level retry in shared HTTP client**

The shared client (OP-2) automatically retries requests on transient
errors (5xx, timeout, connection error) with configurable backoff,
transparent to the fetcher code. HTTP 429 is optionally included.

- Pro: consistent retry behavior for all fetchers without per-spec
  specification
- Pro: fetcher specs only document non-standard behavior (e.g., NVD's
  429 handling, GHSA's abort-on-failure policy)
- Con: may conflict with fetcher-specific retry logic (e.g., NVD
  already specifies inline retry for 429)
- Con: transport retry + Celery task retry can compound (though they
  cover different scopes: single request vs. entire task)

**B) No transport-level retry — keep current per-spec approach**

Each fetcher spec continues to specify its own retry behavior for
individual HTTP requests within `execute()`.

- Pro: maximum control per fetcher
- Con: inconsistent behavior; new fetcher specs may omit retry
  entirely

**C) Standardized retry contract in fetcher-infrastructure.md**

Define a recommended retry pattern for individual HTTP requests
within `execute()` in the base spec (e.g., "retryable conditions,
max attempts, backoff"), but leave implementation to each fetcher.

- Pro: consistent contract without enforcing a specific mechanism
- Con: still requires each fetcher spec to implement it

**Recommendation**: Option A for a conservative default (retry 5xx
and connection errors with fixed backoff; 429 with Retry-After per
OP-9 decision). Fetchers with specific needs override or disable the
transport retry. This is complementary to, not a replacement for,
Celery task retry which operates at a different scope.

#### Retry Amplification Analysis

Transport-level retry interacts with Celery task-level retry. The
worst case must be bounded and understood.

**Retry scopes**:

```
┌─────────────────────────────────────────────────────────┐
│ Celery task retry (fetch_single only)                   │
│ Scope: entire task invocation                           │
│ Trigger: task raises retryable exception after all      │
│          internal attempts fail                         │
│ Policy: 3x with 5s/10s/20s backoff                     │
│                                                         │
│  ┌───────────────────────────────────────────────────┐  │
│  │ Transport-level retry (per HTTP request)          │  │
│  │ Scope: single HTTP request                        │  │
│  │                                                   │  │
│  │ Triggers and policies:                            │  │
│  │ • 5xx, connection error, timeout                  │  │
│  │   → 4 attempts (1 original + 3 retries)          │  │
│  │   with 1s/2s/4s fixed backoff                     │  │
│  │ • 429/503 with Retry-After ≤ 120s                 │  │
│  │   → 1 retry, wait Retry-After value (per OP-9)   │  │
│  │ • 429/503 with Retry-After > 120s                 │  │
│  │   → no retry, propagate error                     │  │
│  │ • 429 without Retry-After                         │  │
│  │   → no retry, propagate to fetcher                │  │
│  └───────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

**Worst case for `fetch_single()` — 5xx scenario**:

- Transport retry: 4 attempts (1 original + 3 retries) = 4 HTTP requests
- If all 4 fail → exception propagates → Celery retries the task
- Celery retry: 3 retries (4 total task executions) × 4 transport
  attempts = **16 total HTTP requests** over ~63s (transport:
  1+2+4=7s per batch × 4 executions = 28s, plus Celery backoff
  5+10+20=35s between retries)

This is acceptable: 16 requests over 63 seconds for a persistently
failing service is not aggressive, and the total time (63s) is well
within the Celery task timeout (3600s default).

**Worst case for `fetch_single()` — 429 with Retry-After scenario**:

- Transport: waits up to 120s, retries once → 2 HTTP requests
- If still 429 → exception propagates → Celery retries the task
- Celery retry: 3 retries (4 total task executions) × 2 transport
  attempts = **8 total HTTP requests** over up to ~515s (4 × up to
  120s wait + 35s Celery backoff)
- 515s is well within the 3600s task timeout

**For `execute()`** (batch fetchers — NVD, Red Hat, GHSA, etc.):

- No Celery task-level retry exists for `execute()` — if the task
  fails, it waits for the next scheduled run
- Transport retry operates independently per request within the batch
- Worst case per request: 4 attempts for 5xx, or 2 attempts for 429
  with Retry-After
- Total for a batch of N requests with persistent failure: up to 4N
  requests before the fetcher's error-handling logic (abort or
  skip-and-continue) takes effect

**No amplification risk for `execute()`**: since there is no Celery
retry at the task level, transport retry only adds resilience against
transient blips (connection reset between pages, momentary 503) without
compounding.

**Operational note**: if a service is persistently returning 5xx,
transport retry adds up to 7 seconds of delay per request (1s + 2s +
4s backoff). For batch fetchers with thousands of requests (Red Hat,
OSV), this can significantly extend run duration. The `run_timeout`
(default 3600s) acts as a safety net, terminating runs that exceed
their expected duration.

**Note on retry counts**: `fetcher-infrastructure.md` defines "Max
retries: 3" for `fetch_single` — this means 3 retries AFTER the
original attempt, resulting in 4 total task executions. Transport
"4 attempts" means 1 original + 3 retries = 4 total HTTP requests per
task execution.

**429 handling rationale** (updated per OP-9 decision):

- 429 **with** `Retry-After`: handled at transport level. The server
  provides explicit guidance — the transport respects it (capped at
  120s, one retry). This replaces NVD's inline 429 retry logic,
  which used fixed backoff and ignored Retry-After
- 429 **without** `Retry-After`: NOT handled at transport level.
  The correct response depends on context — some fetchers want to
  skip and continue (Red Hat), others want to abort (GHSA). The
  fetcher decides

**Status**: `resolved`

**Resolution**: Option A — transport-level retry in shared HTTP client.

The shared client (OP-2) automatically retries transient errors before
the fetcher sees them. If all retries fail, the error propagates to the
fetcher, which applies its own logic (abort, skip-and-continue, etc.).

**Retry policy**:

**Dispatch rule**: when a response matches multiple rows (e.g., 503 is
both a 5xx and may carry `Retry-After`), the most specific row wins. If
`Retry-After` is present and parseable, the guided path is selected;
otherwise, the generic status-code row applies.

| Condition | Retry | Backoff |
|-----------|-------|---------|
| 5xx, connection error, timeout | 4 attempts (1 original + 3 retries) | 1s / 2s / 4s (fixed) |
| 429/503 with `Retry-After` ≤ 120s | 1 retry | Wait the indicated value (per OP-9) |
| 429/503 with `Retry-After` > 120s | No retry | Error propagated immediately |
| 429 without `Retry-After` | No retry at transport | Fetcher decides (knows its source) |
| 4xx (non-429) | No retry | Client error — retrying is pointless |

**Path exclusivity**: if a response enters the Retry-After guided path
(429/503 with `Retry-After` ≤ 120s), the guided retry is the final
attempt. If the retry response would normally qualify for fixed-backoff
retry (e.g., the guided retry returns 503 without `Retry-After`), no
additional fixed-backoff attempts are made — the error is propagated to
the caller. The two retry paths are mutually exclusive within a single
request sequence.

Consequence: a server that sends `Retry-After` on 503 receives one
guided retry, whereas the same 503 without `Retry-After` receives three
fixed-backoff retries. This is intentional: the server's explicit
guidance replaces the client's blind attempts. If the server-guided
retry fails, a more persistent issue is likely — additional immediate
retries at arbitrary intervals are unlikely to improve the outcome.

**Impact on existing fetchers** (all preserved or improved):

| Fetcher | Before | After | Effect |
|---------|--------|-------|--------|
| `sync_nvd_cves` | Inline retry 3x (5s/10s/20s) for 429 only | Transport handles 429+Retry-After (server-guided). 5xx/timeout also retried (4 attempts) | Improved — respects server guidance; covers more error types |
| `sync_ghsa_advisories` | No retry → abort on page failure | Transient 5xx retried 4x before abort logic sees it | Improved — transient blips no longer trigger abort |
| `sync_redhat_cves` / `sync_osv_advisories` | 5xx → `record_failed`, continue | 5xx retried 4x; only persistent failures reach `record_failed` | Improved — fewer false `record_failed` |
| `sync_cisa_kev` | Failure → abort | 4 attempts before abort | Improved — more resilient |
| IBS fetchers | Own retry ("exponential backoff") | IBSClient retry removed; transport retry handles transient failures transparently | Simplified — single well-specified retry layer |

**Override**: fetchers that do NOT want transport retry (hypothetical
future case) can disable it via `http_client_options`.

---

### OP-6: TLS configuration for SUSE internal services

**Problem**: SUSE internal services (IBS at `api.suse.de` and
`download.suse.de`, SMELT at `smelt.suse.de`, AIMAAS at
`aimaas.suse.de`) use HTTPS. The LDAP integration explicitly
specifies TLS validation against the SUSE Trust Root CA at
`certs/SUSE_Trust_Root.crt`. But HTTP-based integrations with SUSE
internal services have no explicit TLS configuration.

**Current state in specs**:
- `ad-integration.md`: explicit — LDAPS with SUSE Trust Root CA,
  `LDAP_CA_CERT_PATH` env var, TLS mandatory
- `ibs-integration.md`: implicit — URLs use `https://` but no CA
  bundle or TLS verification is specified
- `product-catalog.md` (SMELT/AIMAAS): implicit — URLs use `https://`
  but no TLS details
- `fetcher-infrastructure.md:986`: lists `LDAP_CA_CERT_PATH` as
  "Infrastructure — tied to certificate management" but only for LDAP

**Questions to resolve**:
1. Do IBS, SMELT, and AIMAAS use certificates signed by the SUSE
   Trust Root CA, or by a public CA? This determines whether the
   system CA bundle is sufficient or a custom CA bundle is needed.
2. If the SUSE Trust Root CA is required, should there be a single
   `SUSE_CA_CERT_PATH` env var (shared by LDAP and HTTP clients), or
   separate per-protocol variables?
3. Should TLS certificate verification be enforced (fail on invalid
   cert) or permissive (log warning) for internal services?

**Options**:

**A) Shared CA bundle configuration for all SUSE services**

A single env var (e.g., `SUSE_CA_CERT_PATH`, defaulting to the
system CA bundle) configures TLS for all connections to `*.suse.de`
hosts — both LDAP and HTTP. `LDAP_CA_CERT_PATH` becomes an alias or
is deprecated in favor of the shared variable.

- Pro: single configuration point for all SUSE internal connections
- Con: may be too broad if different services have different TLS
  requirements

**B) Per-service TLS configuration**

Each service that requires a custom CA bundle has its own env var
(as LDAP does now). HTTP services that work with the system CA
bundle require no additional configuration.

- Pro: flexible; no unnecessary coupling
- Con: configuration sprawl if multiple services need the same CA

**C) Document that system CA bundle is sufficient**

If IBS, SMELT, and AIMAAS use publicly-trusted certificates (or
certificates that are already in the system CA bundle after
`update-ca-certificates`), explicitly document this in the relevant
specs so it is not an implicit assumption.

- Pro: clarifies the assumption; no new configuration needed
- Con: only works if the assumption is actually correct

**Recommendation**: this requires a factual determination first. The
design decision follows directly from the factual answer — it cannot
be resolved through discussion alone.

#### Action Required

Verify the certificate chain for each SUSE internal HTTP endpoint:

```bash
# Check certificate issuer for each host
openssl s_client -connect api.suse.de:443 -showcerts </dev/null 2>/dev/null | \
  openssl x509 -noout -issuer -subject

openssl s_client -connect download.suse.de:443 -showcerts </dev/null 2>/dev/null | \
  openssl x509 -noout -issuer -subject

openssl s_client -connect smelt.suse.de:443 -showcerts </dev/null 2>/dev/null | \
  openssl x509 -noout -issuer -subject

openssl s_client -connect aimaas.suse.de:443 -showcerts </dev/null 2>/dev/null | \
  openssl x509 -noout -issuer -subject
```

**Decision tree based on results**:

- If all hosts use the SUSE Trust Root CA (same CA as LDAP) → **Option A**
  is the natural choice. Single `SUSE_CA_CERT_PATH` env var, shared
  by LDAP and HTTP clients
- If all hosts use publicly-trusted CAs (DigiCert, Let's Encrypt,
  etc.) → **Option C**. Document explicitly that the system CA bundle
  is sufficient. No new configuration needed
- If mixed (some SUSE CA, some public) → **Option B**. Per-service
  configuration, only for those that need the SUSE CA bundle

In all cases: TLS certificate verification MUST be enforced (not
permissive). A failed TLS handshake is a hard error — the fetcher
should fail immediately with a clear error message, not proceed with
an unverified connection.

**Status**: `resolved`

**Resolution**: Option A — single shared CA configuration for all SUSE
internal services, with combined trust store.

#### Factual Verification (performed 2026-06-22)

All SUSE internal services use certificates signed by the same internal
CA chain:

```
SUSE Trust Root (self-signed, committed at certs/SUSE_Trust_Root.crt)
  └── SUSE CA Root (intermediate)
        └── SUSE CA all 2023.1 (issuing CA)
              └── leaf certificates
```

| Service | Host:Port | Protocol | Issuer | Verified |
|---------|-----------|----------|--------|----------|
| IBS API | `api.suse.de:443` | HTTPS | SUSE CA all 2023.1 | Yes (`Verify return code: 0`) |
| IBS Download | `download.suse.de:443` | HTTPS | SUSE CA all 2023.1 | Yes |
| SMELT | `smelt.suse.de:443` | HTTPS | SUSE CA all 2023.1 | Yes |
| AIMAAS | `aimaas.suse.de:443` | HTTPS | SUSE CA all 2023.1 | Yes |
| RabbitMQ | `rabbit.suse.de:5671` | AMQPS | SUSE CA all 2023.1 | Yes |

All verified with `openssl s_client -CAfile certs/SUSE_Trust_Root.crt`.
Port 5672 (plaintext AMQP) is not available — RabbitMQ requires TLS.

#### Design

- **Env var**: `SUSE_CA_CERT_PATH` (default: `certs/SUSE_Trust_Root.crt`)
  - The SUSE Trust Root CA file is committed in the repository. The
    default path (relative to working directory) works both in
    containers (workdir `/app`) and in local development (run from
    project root). No configuration needed for standard deployments
  - The env var exists as an override for non-standard deployments
    where the cert file is at a different path
- **Combined trust store**: at runtime, Python builds an SSL context
  that includes both the system CA bundle (for public services: NVD,
  GitHub, CISA, Red Hat, OSV, FIRST.org) and the SUSE CA (for internal
  services). All connections use the same trust store — no host
  matching, no fallback, no host list to maintain. The SSL context is
  built at client creation time — once per `execute()` run for
  BaseFetcher's lazy property, and once at `IBSClient` instantiation
  for long-lived clients
- **If `SUSE_CA_CERT_PATH` file does not exist**: the combined trust
  store contains only system CAs. Connections to SUSE internal services
  fail with a clear TLS error. This is the correct behavior for
  environments without internal network access. File existence is
  checked lazily at client creation time, not at application startup.
  However, the application SHOULD emit a log warning at startup if the
  file is absent, alerting operators early without blocking startup
  (fetchers targeting only public APIs do not need it)
- **Scope**: all TLS connections — HTTP (via shared client, OP-2),
  LDAP (sync_ldap_directory fetcher), AMQP (IBSEventConsumer)
- **`LDAP_CA_CERT_PATH` deprecated**: replaced by `SUSE_CA_CERT_PATH`.
  All references to `LDAP_CA_CERT_PATH` in specs and code must be
  updated
- **Container TLS**: the `backend/Dockerfile` continues to install the
  SUSE Trust Root CA into the system trust store via
  `update-ca-certificates`. This ensures that non-Python TLS clients
  (git subprocess for blobless clone downloads, debugging tools like
  `curl`) can validate SUSE internal certificates. Python additionally
  builds its own combined trust store at the application level for
  httpx/LDAP/AMQP connections
- **TLS verification**: always enforced (fail hard). A failed TLS
  handshake is an immediate error — never proceed with an unverified
  connection
- **Certificate rotation**: since the SSL context is built at client
  creation time, long-lived clients (IBSClient) require a process
  restart to pick up a rotated CA certificate. This is acceptable given
  that CA rotations are infrequent (years between)

---

### OP-10: Rate limiting pattern (inter-request delay)

**Problem**: fetchers that make many sequential requests use
configurable delays between requests to stay within API rate limits.
This pattern exists in multiple specs but is not standardized.

**Current state in specs**:

| Fetcher | Delay mechanism | Default | Configurable? |
|---------|----------------|---------|---------------|
| `sync_nvd_cves` | `request_delay_seconds` custom setting | 6.0s (0.6s with API key) | Yes (0.1–30.0) |
| `sync_redhat_cves` | `throttle_delay_seconds` custom setting | 2.0s | Yes (0.1–30.0) |
| `sync_osv_advisories` | `throttle_delay_seconds` custom setting | 0.2s | Yes (0.05–10.0) |
| `sync_ibs_bugowners` | `rate_limit` from `FetcherConfig` | Not specified | Yes (generic field) |
| Other fetchers | Not specified | — | — |

`FetcherConfig` already has a generic `rate_limit` field
(`fetcher-infrastructure.md:968`), but its semantics are not defined
(is it req/s? delay in seconds? a complex object?).

**Options**:

**A) Standardize the FetcherConfig.rate_limit field semantics**

Define `rate_limit` as inter-request delay in seconds (float). The
shared client (OP-2) or `BaseFetcher` applies this delay
automatically between requests. Fetchers that need custom delay
logic (e.g., NVD uses `asyncio.sleep`) continue using custom
settings; the generic `rate_limit` serves as a fallback or default.

- Pro: gives the existing `rate_limit` field a clear meaning;
  operators can tune any fetcher's request rate from the dashboard
- Con: may be too simple for complex rate limiting scenarios (e.g.,
  NVD's sliding window)

**B) Keep per-fetcher custom settings for delay**

Each fetcher that needs rate limiting defines its own custom setting
(`request_delay_seconds`, `throttle_delay_seconds`, etc.) with
per-fetcher naming and semantics.

- Pro: full per-fetcher flexibility
- Con: inconsistent naming; no unified dashboard control

**C) Define rate_limit as a structured object**

`FetcherConfig.rate_limit` is a JSON object with fields like
`{delay_seconds: float, max_requests_per_window: int, window_seconds: int}`.

- Pro: can express complex rate limiting policies
- Con: over-engineered for current needs; none of the current fetchers
  use window-based rate limiting internally

**Recommendation**: Option A for simplicity.

#### Enforcement Responsibility: Client vs. Fetcher

A key design question for Option A: **who applies the delay?**

**Option A1: Client-level enforcement**

The shared HTTP client (OP-2) reads `rate_limit` from the fetcher's
configuration and automatically sleeps between requests.

- Pro: transparent to fetcher code; impossible to forget
- Con: the client needs access to fetcher configuration (coupling)
- Con: single-request fetchers (KEV) should not sleep after their
  only request
- Con: complex scenarios (NVD: different delay depending on API key
  presence) are hard to express through a single float

**Option A2: Fetcher-level enforcement (recommended)**

The delay remains the fetcher's responsibility (via `asyncio.sleep()`
in the pagination/iteration loop). `FetcherConfig.rate_limit` defines
the **value** and makes it operator-tunable from the dashboard, but
the fetcher **applies** it.

- Pro: no coupling between client and fetcher configuration
- Pro: fetcher controls exactly where the delay is applied (e.g.,
  between pages but not between sub-requests within a page)
- Pro: single-request fetchers naturally have no delay (no loop)
- Pro: complex scenarios are easy (NVD reads its custom setting;
  other fetchers read the generic `rate_limit`)
- Con: fetcher authors must remember to apply the delay — but this
  is already the case today

**Naming standardization**: regardless of enforcement model, the
existing inconsistency (`request_delay_seconds` vs
`throttle_delay_seconds` vs `rate_limit`) should be addressed:

- `FetcherConfig.rate_limit` → canonical field name, semantics:
  "minimum inter-request delay in seconds (float)"
- Per-fetcher custom settings → remain where they provide value
  beyond the generic field (e.g., NVD's API-key-dependent delay).
  Their spec documents the relationship to the generic field:
  "overrides `rate_limit` when set"
- New fetcher specs → use `rate_limit` from FetcherConfig unless
  they have a specific reason for a custom setting

**Status**: `resolved`

**Resolution**: Option A with fetcher-level enforcement (A2). The
existing `FetcherConfig.rate_limit` field (VARCHAR, frequency format) is
replaced by a new field with correct semantics.

#### Field definition

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `request_delay` | FLOAT | NOT NULL, DEFAULT 0, CHECK (value >= 0 AND value <= 300) | Minimum inter-request delay in seconds. 0 means no delay. Applied by the fetcher in its pagination/iteration loop via `asyncio.sleep(self.config.request_delay)` |

**Per-fetcher recommended ranges**: each fetcher spec documents its own
recommended range for `request_delay` based on the target API's rate
limits. Setting values outside the recommended range may cause
rate-limit violations (too low) or `run_timeout` termination (too
high). The CHECK constraint (0–300) is a safety net, not a substitute
for per-fetcher guidance. Operators should consult the fetcher spec
before adjusting this value.

#### Design decisions

- **Name**: `request_delay` — describes what the value IS (a delay in
  seconds), not what it achieves. Unambiguous about semantics
- **Replaces**: `FetcherConfig.rate_limit` (VARCHAR, format `"2/s"`,
  `"100/m"`) — removed entirely. The frequency format was never
  implemented and is harder to reason about than a simple delay
- **Default**: 0 (no delay). Fetchers that need throttling declare a
  per-fetcher default at registration. Fetchers toward internal SUSE
  services (no rate limits) naturally use 0
- **Upper bound**: 300 seconds (5 minutes). A delay longer than 5
  minutes per request is almost certainly a misconfiguration — it would
  make even a small batch take hours. The admin dashboard validates
  this constraint on PATCH
- **Enforcement**: fetcher-level. The fetcher applies
  `asyncio.sleep(self.config.request_delay)` in its iteration loop.
  The shared HTTP client (OP-2) does NOT enforce the delay — it has
  no knowledge of where delays should be inserted (between pages?
  between sub-requests? after each request?)
- **Runtime-configurable**: yes, via the admin dashboard (FetcherConfig
  PATCH endpoint). The operator can adjust the delay without code
  changes or redeployment
- **Registration behavior**: `INSERT ... ON CONFLICT DO NOTHING` (per
  existing spec). Operator overrides persist across deployments.
  Default values from the spec are only installed on first registration

#### Migration of custom settings

The per-fetcher custom settings that existed because no centralized
field was available are eliminated:

| Fetcher | Old custom setting | New FetcherConfig default |
|---------|-------------------|--------------------------|
| `sync_nvd_cves` | `request_delay_seconds` (6.0) | `request_delay` = 6.0 (conservative: safe without API key. Operator reduces to 0.6 after configuring API key) |
| `sync_redhat_cves` | `throttle_delay_seconds` (2.0) | `request_delay` = 2.0 |
| `sync_osv_advisories` | `throttle_delay_seconds` (0.2) | `request_delay` = 0.2 |
| `sync_ibs_bugowners` | used `rate_limit` generically | `request_delay` = 0 (no delay needed for internal IBS API) |

#### Additional rename: `timeout_seconds` → `run_timeout`

While updating FetcherConfig fields, the existing `timeout_seconds`
field is renamed to `run_timeout` for clarity:

- **Rationale**: `timeout_seconds` is generic ("timeout of what?").
  `run_timeout` makes it clear it refers to the maximum duration of a
  fetcher run — the same concept used for both Celery soft time limit
  enforcement and stale run detection
- **Not Celery-specific**: the field is consumed by Celery (soft time
  limit), the API trigger endpoint (stale detection), the dashboard
  (stale hint), and the CLI. Naming it after the application concept
  (run timeout) rather than the enforcement mechanism (Celery timeout)
  is more accurate
- **Consistent with `request_delay`**: both fields use the
  `<noun>_<noun>` pattern without a `_seconds` suffix. Unit (seconds)
  is documented in the column description
- **Default**: 3600 (unchanged). 1 hour is a reasonable safety net
  for the majority of fetchers. Fetchers that are known to be fast
  (kernel: 300, LDAP: 900) override with a tighter value for faster
  stale detection
- **Type**: INTEGER, NOT NULL (unchanged)

---

### OP-11: HTTP proxy support

**Problem**: in enterprise deployment environments, outgoing HTTP
traffic may be routed through a forward proxy. If the shared HTTP
client does not support proxy configuration, fetchers cannot reach
external services in such environments.

**Current state in specs**: no fetcher spec or infrastructure document
mentions HTTP proxy support. The deployment guide
(`docs/deployment.md`) does not reference proxy configuration.

**Current state in implementation**: httpx natively respects the
standard environment variables `HTTPS_PROXY`, `HTTP_PROXY`, and
`NO_PROXY` when creating a client with default transport. No code
changes are needed to support proxies — only documentation and
explicit acknowledgment.

**Options**:

**A) Document proxy support as inherited from httpx defaults**

The shared HTTP client specification (OP-2) explicitly documents
that proxy configuration is provided through standard environment
variables. No custom configuration, no custom env vars, no
per-fetcher logic.

Environment variables:
- `HTTPS_PROXY` — proxy URL for HTTPS requests (e.g.,
  `http://proxy.corp:3128`)
- `HTTP_PROXY` — proxy URL for HTTP requests (rarely needed —
  Sentinel targets are all HTTPS)
- `NO_PROXY` — comma-separated list of hosts that bypass the proxy
  (e.g., `localhost,127.0.0.1`)

- Pro: zero implementation effort — httpx already does this
- Pro: standard mechanism understood by operations teams
- Pro: works for all fetchers and non-fetcher HTTP components
  (IBSClient, SSO) without per-component configuration
- Con: none meaningful

**B) Custom proxy configuration via Sentinel-specific env vars**

Define Sentinel-specific env vars (e.g., `SENTINEL_HTTP_PROXY`) that
are passed to the HTTP client explicitly.

- Pro: explicit control within Sentinel configuration
- Con: reinvents the standard; operations teams expect `HTTPS_PROXY`
- Con: implementation effort for no benefit

**C) No proxy support**

- Pro: no documentation needed
- Con: deployment in proxy-required environments is blocked; no
  documentation means operators discover the limitation at runtime

**Recommendation**: Option A.

This is purely a documentation concern. The spec for the shared HTTP
client (OP-2) should include a "Proxy Configuration" paragraph
stating that standard proxy env vars are respected. Additionally,
`docs/configuration.md` should list `HTTPS_PROXY`, `HTTP_PROXY`, and
`NO_PROXY` in the environment variables index with a note that they
are standard (not Sentinel-specific) and apply to all outgoing HTTP
connections.

**Status**: `resolved`

**Resolution**: Option A — document proxy support as inherited from
httpx defaults.

- httpx natively respects `HTTPS_PROXY`, `HTTP_PROXY`, and `NO_PROXY`
  environment variables. No code changes needed
- These are standard system-level environment variables, not
  Sentinel-specific configuration. They are set at the container or
  system level, not in application configuration
- Documentation only: add a "Proxy Configuration" paragraph to the
  HTTP client defaults section in `fetcher-infrastructure.md`, and
  list the variables in `docs/configuration.md` with a note marking
  them as standard (not Sentinel-specific)
- No `.env.example` entry — these are system-level vars that
  operators familiar with proxy setup already know about
- **TLS-intercepting proxies**: if the deployment uses a forward proxy
  that terminates and re-encrypts TLS (common in enterprise
  environments), the proxy's CA certificate must be present in the
  system CA bundle. The combined trust store includes system CAs, so
  this is handled by standard proxy CA installation procedures (no
  Sentinel-specific configuration needed)

---

## Application Plan

This section maps each open point to the specifications that need
updating once the point is resolved. The plan is updated incrementally
as decisions are made.

### Specs affected by open point

| Spec file | OP-1 | OP-2 | OP-3 | OP-4 | OP-5 | OP-6 | OP-7 | OP-8 | OP-9 | OP-10 | OP-11 |
|-----------|------|------|------|------|------|------|------|------|------|-------|-------|
| `platform/fetcher-infrastructure.md` | Y | Y | Y | Y | Y | Y | Y | — | Y | Y | Y |
| `platform/fetcher-operations.md` | — | — | — | — | — | — | — | — | — | Y | — |
| `integrations/ibs-integration.md` | — | Y | — | Y | — | Y | Y | — | Y | — | — |
| `integrations/ibs-rabbitmq-integration.md` | — | — | — | — | — | Y | — | — | — | — | — |
| `identity/ad-integration.md` | — | — | — | — | — | Y | — | — | — | — | — |
| `tickets/cve-sync-nvd.md` | — | — | — | Y | — | — | — | — | Y | Y | — |
| `tickets/cve-sync-redhat.md` | — | — | — | Y | — | — | — | — | — | Y | — |
| `tickets/cve-sync-ghsa.md` | — | — | — | Y | — | — | — | — | — | — | — |
| `tickets/cve-sync-osv.md` | — | — | — | Y | — | — | — | — | — | Y | — |
| `tickets/cve-sync-kev.md` | — | — | Y | — | — | — | — | Y | — | — | — |
| `tickets/cve-sync-epss.md` | — | — | — | — | — | — | Y | Y | — | — | — |
| `tickets/cve-sync-kernel.md` | — | — | — | — | — | — | — | — | — | Y | — |
| `packages/product-catalog.md` | — | — | — | — | — | Y | — | — | — | — | — |
| `packages/ibs-track-release-detection.md` | — | — | — | — | — | — | — | — | — | — | — |
| `packages/ibs-product-release-detection.md` | — | — | Y | — | — | — | — | Y | — | — | — |
| `packages/package-bugowner.md` | — | — | — | — | — | — | — | — | — | Y | — |
| `packages/ibs-submission-tracking.md` | — | — | — | — | — | — | — | — | — | — | — |
| `docs/configuration.md` | — | — | — | — | — | Y | — | — | — | Y | Y |
| `docs/conventions.md` | — | — | — | — | — | Y | — | — | — | — | — |
| `docs/data-model.md` | — | — | — | — | — | — | — | — | — | Y | — |
| `docs/data-sources.md` | — | — | — | — | — | — | — | — | — | Y | — |
| `docs/deployment.md` | — | — | — | — | — | Y | — | — | — | — | — |

All spec paths are relative to `docs/features/` unless otherwise noted.

**Matrix interpretation**: a `Y` cell means the spec is *affected* by
the open point's resolution. Not all affected specs require textual
changes — some benefit transparently without conflicting with their
current text. Specifically:

- **OP-4 for `cve-sync-ghsa.md`, `cve-sync-redhat.md`,
  `cve-sync-osv.md`**: transport-level retry improves resilience for
  these fetchers, but their current error handling descriptions remain
  correct — they describe fetcher-level behavior (what happens after
  transport retry is exhausted). No textual changes needed. Only specs
  with *conflicting* inline retry logic (NVD, IBS) require modification
- **OP-7 for `cve-sync-epss.md`**: the `Accept: application/json`
  default aligns with the EPSS fetcher's JSON consumption. The spec is
  a stub (TBD) — no changes needed now; the default will apply when the
  spec is designed
- **OP-8 for `cve-sync-kev.md`, `cve-sync-epss.md`,
  `ibs-product-release-detection.md`**: OP-8 was resolved as "defer
  entirely". These specs are marked because they were discussed during
  OP-8 analysis, but no changes result from the deferral.
  `ibs-product-release-detection.md` retains its own spec-level open
  item for future ETag/caching design

### Detailed Execution Plan

This section contains the exact modifications to apply. Each step is
self-contained and specifies the precise text to add, remove, or
replace. Steps MUST be executed in order (later steps depend on earlier
ones being complete).

---

#### Step 1: Add "Shared HTTP Client" section to `fetcher-infrastructure.md`

**Location**: insert as a new `##` section immediately after the
"Custom Settings Schema" section (after line ~1235, before
"## BaseCVEFetcher Class").

**Rationale**: the HTTP client is a BaseFetcher capability, but its
full specification is too large to inline in the "BaseFetcher Base
Class" list. It is placed after Custom Settings Schema because it
references `http_client_options` overrides (a parallel concept).

**Full text to insert**:

```markdown
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
   boilerplate.

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

#### TLS Configuration

See the peer-level "TLS Trust Store Configuration" section (below) for
the full specification. The shared HTTP client uses the combined trust
store (system CAs + SUSE CA) by default — no per-fetcher configuration
needed.

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

### BaseFetcher Integration

#### Lazy Property: `self.http_client`

```python
class BaseFetcher:
    http_client_options: ClassVar[dict] = {}

    @property
    def http_client(self) -> httpx.AsyncClient:
        """Pre-configured HTTP client, created on first access."""
        if self._http_client is None:
            self._http_client = create_http_client(
                name=self.name, **self.http_client_options
            )
        return self._http_client
```

- Created lazily on first access during `execute()`
- Connection pooling active for the entire run
- Destroyed by `BaseFetcher.run()` in the `finally` block (after
  `record_end()`, suppressing `aclose()` exceptions with a log warning)
- Between runs: no client exists, no idle connections
- Stale connection handling: httpx closes idle connections after ~5s of
  inactivity within the pool. If a server closes a connection earlier,
  httpx transparently opens a new one on the next request
- If never accessed during a run: teardown is a no-op

#### Override Mechanism

Fetchers with non-standard requirements override via a class attribute:

```python
class ProductReleaseFetcher(BaseFetcher):
    http_client_options = {"timeout": httpx.Timeout(10.0, read=120.0)}
```

Merge semantics: `http_client_options` entries are keyword arguments to
the factory. For same-key headers, the fetcher-specific value replaces
the factory default (last-writer-wins). User-Agent is the sole exception
— always preserved and cannot be overridden. Other options (timeout,
transport) replace defaults at the top-level kwarg level (not
deep-merged).

#### `fetch_single()` and `catch_up()` Lifecycle

`fetch_single()` is safe to call from any context:

- If `self._http_client` exists (inside an active `run()` → `execute()`
  flow): reuses it. Connection pooling preserved for Pattern B fetchers
  (Red Hat, OSV) that call `fetch_single()` in a loop
- If `self._http_client` does not exist (standalone invocation from a
  task wrapper, test, or any future call site): creates a temporary
  client for the duration of the call, closes on return
- `catch_up()` inherits this behavior (delegates to `fetch_single()`)
- Error handling: if temporary client creation fails (e.g., TLS
  misconfiguration), the exception propagates normally. No cleanup needed
  for a client that was never created

No caller responsibility: task wrappers (`fetch_single_cve`,
`run_catch_up`) do not manage HTTP client lifecycle.

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
```

**Additionally**, insert a new peer-level `##` section immediately after
`## Shared HTTP Client` (before `## BaseCVEFetcher Class`):

```markdown
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
```

Additionally, add a brief item 5 to the "BaseFetcher Base Class" list
(after item 4 "Enabled check" at line ~113):

```markdown
5. **Shared HTTP client**: a pre-configured `self.http_client` lazy
   property for outgoing HTTP requests. See "Shared HTTP Client" section
   for the full specification.
```

---

#### Step 2: Rename `timeout_seconds` → `run_timeout` across all specs

**Verification**: `grep -rn "timeout_seconds" docs/features/ docs/data-model.md docs/configuration.md`

**Affected files and known occurrences**:

| File | Occurrences | Action |
|------|-------------|--------|
| `platform/fetcher-infrastructure.md` | ~12 (table definition, stale run section, audit events, custom settings intro, log example) | find/replace all |
| `platform/fetcher-operations.md` | ~8 (line 449 error desc, lines 513/583 JSON examples, line 594 validation, line 631 field list, lines 814/816 stale hint, line 911 CLI warning) | find/replace all |
| `tickets/cve-sync-kernel.md` | 1 (line 330) | find/replace |
| `docs/data-model.md` | 2 (line 322 ER diagram, line 1410 table) | find/replace |

**Replace**: `timeout_seconds` → `run_timeout` (verbatim, all occurrences).

---

#### Step 3: Replace `rate_limit` field with `request_delay` across all specs

**Verification**: `grep -rn "rate_limit" docs/features/ docs/data-model.md docs/data-sources.md docs/configuration.md`

This is NOT a simple rename — the semantics change (VARCHAR frequency
format → FLOAT seconds). Each occurrence needs context-appropriate
replacement.

**Affected files and actions**:

| File | Location | Current text (excerpt) | Replacement |
|------|----------|----------------------|-------------|
| `fetcher-infrastructure.md` | FetcherConfig table (~line 2781) | `rate_limit \| VARCHAR(20) \| nullable \| Rate limit expression...` | `request_delay \| FLOAT \| NOT NULL, DEFAULT 0 \| Minimum inter-request delay in seconds...` (full replacement in Step 2's table context) |
| `fetcher-infrastructure.md` | Custom settings intro (~line 990) | `...generic FetcherConfig fields (enabled, schedule_override, timeout_seconds, rate_limit)...` | `...(enabled, schedule_override, run_timeout, request_delay)...` |
| `fetcher-infrastructure.md` | Audit event detail (~line 2834) | `..."field" is schedule_override, timeout_seconds, or rate_limit` | `..."field" is schedule_override, run_timeout, or request_delay` |
| `fetcher-operations.md` | JSON examples (lines 514, 584) | `"rate_limit": null` / `"rate_limit": "2/s"` | `"request_delay": 0` / `"request_delay": 2.0` |
| `fetcher-operations.md` | Validation (line 597) | `rate_limit: must match the pattern "<number>/<unit>"...` | `request_delay: must be a float >= 0 and <= 300` |
| `fetcher-operations.md` | Field list (line 631) | `...standard fields (schedule_override, timeout_seconds, rate_limit)...` | `...(schedule_override, run_timeout, request_delay)...` |
| `fetcher-operations.md` | Error table (line 657) | `Invalid cron expression, timeout, or rate limit format` | `Invalid cron expression, run_timeout, or request_delay value` |
| `fetcher-operations.md` | CLI output (line 855) | `Rate limit: —` | `Request delay: 0s` |
| `packages/package-bugowner.md` | Lines 307, 325 | `Respect the rate_limit from FetcherConfig...` | `Respect the request_delay from FetcherConfig between IBS API calls via asyncio.sleep(self.config.request_delay)` |
| `docs/data-model.md` | Table (line 1411) | `rate_limit \| VARCHAR(20) \| nullable \| Rate limit...` | `request_delay \| FLOAT \| NOT NULL, DEFAULT 0 \| Minimum inter-request delay in seconds. CHECK (>= 0 AND <= 300).` |
| `docs/data-model.md` | ER diagram (line 322-323) | (may not contain `rate_limit` — verify) | Add `FLOAT request_delay "DEFAULT 0"` if absent |
| `docs/data-sources.md` | Line 966 | `Admin-configurable via FetcherConfig.rate_limit` | `Admin-configurable via FetcherConfig.request_delay` |

---

#### Step 4: Remove "Retry-After ignored" note in `fetcher-infrastructure.md`

**Verification**: `grep -n "Retry-After" docs/features/platform/fetcher-infrastructure.md`

**Find and remove** any paragraph containing:

> "This design deliberately ignores the Retry-After header for
> simplicity."

If not present (already removed in a prior edit), skip this step.

---

#### Step 5: Replace `LDAP_CA_CERT_PATH` → `SUSE_CA_CERT_PATH` across all specs

**Verification**: `grep -rn "LDAP_CA_CERT_PATH" docs/`

**Affected files**:

| File | Action |
|------|--------|
| `platform/fetcher-infrastructure.md` (~line 1008) | Replace in env var exclusion table |
| `identity/ad-integration.md` (line 36) | Replace in connection description |
| `docs/configuration.md` (line 106) | Replace entire row (see Step 19a below for full replacement text) |
| `docs/conventions.md` (line 61) | Remove `LDAP_CA_CERT_PATH` from LDAP row examples. Keep `LDAP_URI` |
| `docs/deployment.md` (line 363) | Replace with `SUSE_CA_CERT_PATH` |

---

#### Step 6: Replace `throttle_delay_seconds` / `request_delay_seconds` custom settings

**Verification**: `grep -rn "throttle_delay_seconds\|request_delay_seconds" docs/`

This step eliminates per-fetcher custom settings that are superseded by
`FetcherConfig.request_delay`. The change has three categories:

**6a. Remove from fetcher spec Settings classes**:

| File | Action |
|------|--------|
| `cve-sync-nvd.md` (lines 46-49) | Remove `request_delay_seconds` field. Keep `results_per_page`. Settings class is retained (still has one field) |
| `cve-sync-redhat.md` (lines 256-260) | Remove entire `class Settings(BaseModel)` block (no fields remain) |
| `cve-sync-osv.md` (lines 291-295) | Remove entire `class Settings(BaseModel)` block (no fields remain) |

**6b. Remove from fetcher spec custom settings tables**:

| File | Old row | Replacement note |
|------|---------|-----------------|
| `cve-sync-nvd.md` (line 91) | `request_delay_seconds \| float \| 6.0 \| 0.1–30.0 \| ...` | Remove row. Add note: "The inter-request delay is configured via `FetcherConfig.request_delay` (default: 6.0, registered at first startup). Reduce to 0.6 after configuring the NVD API key." |
| `cve-sync-redhat.md` (line 401) | `throttle_delay_seconds \| float \| 2.0 \| 0.1–30.0 \| ...` | Remove entire Custom Settings table. Add note: "This fetcher has no custom settings. The inter-request delay is configured via `FetcherConfig.request_delay` (default: 2.0)." |
| `cve-sync-osv.md` (line 440) | `throttle_delay_seconds \| float \| 0.2 \| 0.05–10.0 \| ...` | Remove entire Custom Settings table. Add note: "This fetcher has no custom settings. The inter-request delay is configured via `FetcherConfig.request_delay` (default: 0.2)." |

**6c. Replace usage in algorithm pseudocode**:

| File | Find | Replace |
|------|------|---------|
| `cve-sync-nvd.md` (lines 136, 176, 221) | `self.get_setting("request_delay_seconds")` | `self.config.request_delay` |
| `cve-sync-redhat.md` (line 291) | `self.settings.throttle_delay_seconds` | `self.config.request_delay` |
| `cve-sync-osv.md` (line 337) | `self.settings.throttle_delay_seconds` | `self.config.request_delay` |

**6d. Replace in `fetcher-infrastructure.md` examples** (Custom Settings Schema section):

| Location | Current | Replacement |
|----------|---------|-------------|
| Lines 1021-1033 (Settings class example) | `SyncRedhatCves` with `throttle_delay_seconds` | `SyncNvdCves` with `results_per_page` (2000, ge=100, le=2000) |
| Line 1150 (get_setting example) | `delay = self.get_setting("throttle_delay_seconds")  # returns DB value or 2.0` | `page_size = self.get_setting("results_per_page")  # returns DB value or 2000` |
| Line 1451 (Pattern B execute loop) | `await asyncio.sleep(self.get_setting("throttle_delay_seconds"))` | `await asyncio.sleep(self.config.request_delay)` |
| Lines 2848-2849 (audit event example) | `schedule_override, timeout_seconds, and custom_settings.throttle_delay_seconds produces three` | `schedule_override, run_timeout, and custom_settings.results_per_page produces three` |

**6e. Replace in `fetcher-operations.md` examples**:

| Location | Current | Replacement |
|----------|---------|-------------|
| Line 199 (list overview) | `"throttle_delay_seconds": 5.0` | `"results_per_page": 500` |
| Lines 508-530 (GET response example) | `sync_redhat_cves` with `throttle_delay_seconds` in custom_settings and settings_schema | Change to `sync_nvd_cves` with `results_per_page` in both custom_settings and settings_schema |
| Line 586 (PATCH example) | `"throttle_delay_seconds": 5.0` | `"results_per_page": 500` |
| Line 611 (reset example) | `{"custom_settings": {"throttle_delay_seconds": null}}` | `{"custom_settings": {"results_per_page": null}}` |
| Lines 858, 884 (CLI output) | `throttle_delay_seconds = 5.0  (default: 2.0, range: 0.1–30.0)` | `results_per_page = 500  (default: 2000, range: 100–2000)` |

**6f. Update `docs/configuration.md`** (line 131):

Replace: `"consider reducing the sync_nvd_cves fetcher's request_delay_seconds custom setting from 6.0s to ~0.6s via the admin dashboard"`

With: `"consider reducing the sync_nvd_cves fetcher's request_delay from 6.0s to ~0.6s via the admin dashboard"`

---

#### Step 7: Update NVD 429 handling in `cve-sync-nvd.md`

**Find** (lines 225-245, the inline retry block):

```markdown
**HTTP 429 handling**: when a page request returns HTTP 429 (Too Many
Requests), the fetcher retries that single request with exponential
backoff:
...
```

**Replace with**:

```markdown
**HTTP 429 handling**: transport-level retry handles 429 responses with
`Retry-After` automatically (server-guided wait + 1 retry, see
`fetcher-infrastructure.md`, Shared HTTP Client — Transport-Level
Retry). If the transport retry resolves the 429, execution continues
transparently.

If 429 persists after transport handling (no `Retry-After` present,
`Retry-After` exceeds 120s cap, or guided retry failed): the fetcher
applies a pre-calculated delay of 30 seconds (NVD's documented
rate-limit window duration) and retries the page once. If the retry
also returns 429: abort run (`status = failure`). The cursor does not
advance — the next scheduled run retries the same time window.
```

Also update `fetch_single()` rate limiting section (line ~618):

**Find**: `request_delay_seconds sleep. HTTP 429 is handled by the Celery retry policy`

**Replace with**: `request_delay sleep. HTTP 429 with Retry-After (≤ 120s) is handled transparently by transport-level retry; 429 without Retry-After (or with Retry-After exceeding the 120s cap) propagates to the Celery retry policy`

---

#### Step 8: Update `cve-sync-kev.md` — remove redundant timeout

**Find** the HTTP timeout row in the fetcher properties table:
`| HTTP timeout | 30 seconds |`

**Remove** this row (now matches the centralized default — redundant).

**Find** any reference to `(timeout: 30 seconds)` in the algorithm
section and remove the parenthetical.

**Find** in the error handling table: `Request timeout (>30s)`
**Replace with**: `Request timeout`

---

#### Step 9: Update `ibs-integration.md` — remove retry, add TLS/Accept

**9a. Remove inline retry rule**.

**Find** (line 241):
```
2. IBS API calls use retry logic with exponential backoff
```

**Remove** this entire business rule line. Transport-level retry in the
shared factory handles transient failures transparently.

**9b. Add IBSClient infrastructure notes**.

**Find** a suitable location in the IBSClient section (near the
connection/configuration description) and add:

```markdown
**HTTP client infrastructure**: `IBSClient` uses the standalone HTTP
client factory (`backend/app/services/http_client.py`) directly, with
the following overrides:

- `Accept: application/xml` (IBS API returns XML, not JSON)
- TLS validated against the SUSE Trust Root CA via the combined trust
  store (see `fetcher-infrastructure.md`, TLS Trust Store
  Configuration)
- Transport-level retry active (4 attempts for 5xx/timeout/connection
  errors)
- Long-lived client: instantiated per-process, not per-request
```

---

#### Step 10: Update `ibs-rabbitmq-integration.md` — add TLS reference

**Find** a suitable location near the AMQPS connection description and
add a note:

```markdown
The AMQPS connection to `rabbit.suse.de:5671` validates TLS against the
SUSE Trust Root CA via `SUSE_CA_CERT_PATH` (see
`fetcher-infrastructure.md`, TLS Trust Store Configuration).
```

---

#### Step 11: Update `ad-integration.md` — rename env var

**Find** (line 36):
```
store at build time. The `LDAP_URI` and `LDAP_CA_CERT_PATH` environment
variables control the connection (see `docs/configuration.md`).
```

**Replace with**:
```
store at build time. The `LDAP_URI` and `SUSE_CA_CERT_PATH` environment
variables control the connection (see `docs/configuration.md`).
```

---

#### Step 12: Update `product-catalog.md` — add TLS note

**Find** a suitable location in the SMELT/AIMAAS connection description
and add:

```markdown
HTTPS connections to SMELT (`smelt.suse.de`) and AIMAAS
(`aimaas.suse.de`) are validated via the combined trust store (system
CAs + SUSE Trust Root CA). See `fetcher-infrastructure.md`, TLS Trust
Store Configuration.
```

---

#### Step 13: Update `package-bugowner.md` — update rate_limit references

**Verification**: `grep -n "rate_limit" docs/features/packages/package-bugowner.md`

**Find** (lines 307 and 325): all references to `rate_limit`.

**Replace with**:
```
Respects `request_delay` from `FetcherConfig` between IBS API calls
via `asyncio.sleep(self.config.request_delay)`.
```

---

#### Step 14: Update `cve-sync-kernel.md` — rename timeout_seconds

**Find** (line 330): `operator MUST increase timeout_seconds for the fetcher`

**Replace with**: `operator MUST increase run_timeout for the fetcher`

---

#### Step 15: Update `ibs-product-release-detection.md` — add timeout override

**Find** the fetcher properties table and add a row:

```
| HTTP read timeout | 120 seconds (override) | `updateinfo.xml` files can be several MB for products with years of accumulated advisories |
```

---

#### Step 16: Update `fetcher-operations.md` — comprehensive field renames

**Verification**: `grep -n "timeout_seconds\|rate_limit\|throttle_delay" docs/features/platform/fetcher-operations.md`

All changes in this file (in addition to those covered in Steps 2, 3, 6e):

| Line | Find | Replace |
|------|------|---------|
| 449 | `timeout_seconds > 0` | `run_timeout > 0` |
| 513 | `"timeout_seconds": 3600,` | `"run_timeout": 3600,` |
| 514 | `"rate_limit": null,` | `"request_delay": 0,` |
| 583 | `"timeout_seconds": 600,` | `"run_timeout": 600,` |
| 584 | `"rate_limit": "2/s",` | `"request_delay": 2.0,` |
| 594 | `timeout_seconds: must be a non-negative integer...` | `run_timeout: must be a non-negative integer...` |
| 597 | `rate_limit: must match the pattern...` (entire validation rule) | `request_delay: must be a float >= 0 and <= 300` |
| 631 | `timeout_seconds`, `rate_limit`): one event with` | `run_timeout`, `request_delay`): one event with` |
| 657 | `Invalid cron expression, timeout, or rate limit format` | `Invalid cron expression, run_timeout, or request_delay value` |
| 814 | `timeout_seconds > 0` | `run_timeout > 0` |
| 816 | `timeout_seconds = 0` | `run_timeout = 0` |
| 855 | `Rate limit: —` | `Request delay: 0s` |
| 911 | `When timeout_seconds is 0` | `When run_timeout is 0` |

---

#### Step 17: Update `data-sources.md` — Fetcher Registry

**Verification**: `grep -n "rate_limit\|request_delay" docs/data-sources.md`

**Find** (line 966): `Admin-configurable via FetcherConfig.rate_limit`

**Replace with**: `Admin-configurable via FetcherConfig.request_delay`

---

#### Step 18: Update `docs/data-model.md` — FetcherConfig table and ER diagram

**18a. Table** (line 1410-1411):

**Find**:
```
| timeout_seconds   | INTEGER     | NOT NULL, DEFAULT 3600 | Max execution time in seconds. Also used as stale run detection threshold. 0 disables both. |
| rate_limit        | VARCHAR(20)  | nullable           | Rate limit (e.g., `"2/s"`, `"100/m"`) |
```

**Replace with**:
```
| run_timeout       | INTEGER     | NOT NULL, DEFAULT 3600 | Max execution time in seconds. Also used as stale run detection threshold and Celery soft time limit. 0 disables both. |
| request_delay     | FLOAT       | NOT NULL, DEFAULT 0  | Minimum inter-request delay in seconds. 0 = no delay. CHECK (>= 0 AND <= 300). |
```

**18b. ER diagram** (line 322):

**Find**: `INTEGER timeout_seconds "DEFAULT 3600"`

**Replace with**:
```
INTEGER run_timeout "DEFAULT 3600"
FLOAT request_delay "DEFAULT 0"
```

(Note: `rate_limit` was already absent from the ER diagram.)

---

#### Step 19: Update `docs/configuration.md`

**19a. Replace `LDAP_CA_CERT_PATH`**:

**Find** (line 106): the `LDAP_CA_CERT_PATH` row.

**Replace with**:
```
| `SUSE_CA_CERT_PATH` | string | `certs/SUSE_Trust_Root.crt` | Path to SUSE internal CA certificate for TLS validation of all connections to *.suse.de services (HTTP, LDAP, AMQP). Combined with system CA bundle at runtime. | `docs/features/platform/fetcher-infrastructure.md` |
```

**19b. Add proxy variables** (in a "Standard (non-Sentinel)" section
or with a note):

```
| `HTTPS_PROXY` | string | (none) | Standard. Proxy URL for outgoing HTTPS connections. Respected by all HTTP clients. | — |
| `HTTP_PROXY` | string | (none) | Standard. Proxy URL for outgoing HTTP connections. | — |
| `NO_PROXY` | string | (none) | Standard. Comma-separated hosts that bypass the proxy. | — |
```

**19c. Update NVD_API_KEY description** (line 131):

**Find**: `request_delay_seconds custom setting from 6.0s to ~0.6s via the admin dashboard`

**Replace with**: `request_delay from 6.0s to ~0.6s via the fetcher admin dashboard`

---

#### Step 20: Update `docs/conventions.md` — LDAP terminology

**Find** in the AD/LDAP/SSO terminology table, LDAP row (line 61),
the examples that mention `LDAP_CA_CERT_PATH`.

**Remove** `LDAP_CA_CERT_PATH` from the examples. Keep `LDAP_URI` as
the LDAP-scoped env var example.

---

#### Step 21: Update `docs/deployment.md` — cert reference

**Find** any reference to `LDAP_CA_CERT_PATH` (line 363).

**Replace with** `SUSE_CA_CERT_PATH`.

---

#### Step 22: Update `docs/drafts/epss-fetcher-workplan.md`

**Verification**: `grep -n "throttle_delay_seconds" docs/drafts/epss-fetcher-workplan.md`

All references to `throttle_delay_seconds` in the EPSS draft must be
updated to use `FetcherConfig.request_delay` instead. Specifically:

- Line 240: `self.get_setting("throttle_delay_seconds")` → `self.config.request_delay`
- Lines 298, 300: custom settings table entry → replace with note about `FetcherConfig.request_delay`
- Lines 471, 520: references to the custom setting → update description

(This draft is WIP and may be redesigned, but it should not reference
a pattern that no longer exists.)

---

#### Step 23: Final verification sweep

After all modifications, run these verification commands to ensure no
stale references remain:

```bash
# Must return 0 matches (excluding this draft and docs/reviews/):
grep -rn "timeout_seconds" docs/features/ docs/data-model.md docs/configuration.md docs/deployment.md
grep -rn "rate_limit" docs/features/ docs/data-model.md docs/data-sources.md
grep -rn "LDAP_CA_CERT_PATH" docs/ --exclude-dir=reviews --exclude="http-client-infrastructure.md"
grep -rn "throttle_delay_seconds\|request_delay_seconds" docs/features/

# Must return only docs/reviews/ (historical, not updated):
grep -rn "timeout_seconds\|rate_limit\|LDAP_CA_CERT_PATH" docs/reviews/
```

If any matches remain, address them before proceeding.

---

#### Step 24: Run reviewers

After all modifications are applied, run the following reviewers to
verify coherence:

1. `@spec-coherence-reviewer` on `fetcher-infrastructure.md` — verify
   the new Shared HTTP Client and TLS Trust Store Configuration sections
   do not contradict existing sections (retry policy for `fetch_single`,
   custom settings, error handling)
2. `@spec-coherence-reviewer` on `cve-sync-nvd.md` — verify the updated
   429 handling is consistent with the transport-level retry defined in
   `fetcher-infrastructure.md`
3. `@docs-reviewer` on the set of modified files — verify documentation
   completeness and cross-reference accuracy

If reviewers identify issues rated "Needs revision", fix them before
considering the application complete.

---

#### Step 25: Delete this draft

Once all modifications are applied and verified:

```
rm docs/drafts/http-client-infrastructure.md
```

The decisions and their rationale are now embedded in the approved
specifications. The draft has served its purpose and must not remain
as a parallel source of truth.

---

## Cross-references

- `docs/features/platform/fetcher-infrastructure.md` — BaseFetcher
  contract, FetcherConfig, retry policy, error handling
- `docs/features/integrations/ibs-integration.md` — IBSClient,
  IBS authentication
- `docs/configuration.md` — environment variables index
- `docs/drafts/epss-fetcher-workplan.md` — OP-12 (precursor to this
  draft's OP-1/OP-2/OP-3)
- `docs/architecture.md` — deployment portability, external
  integrations overview
- `docs/data-sources.md` — external service catalog
- httpx documentation — https://www.python-httpx.org/ (implementation
  reference for client lifecycle, connection pooling, proxy support,
  timeout configuration, transport-level retry via custom transports)
