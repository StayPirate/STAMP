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
services**. It does NOT cover:

- LDAP connections (`sync_ldap_directory` — uses LDAP protocol, not HTTP)
- AMQP connections (IBS RabbitMQ consumer — uses AMQP protocol)
- Git operations (MITRE, Kernel fetchers — use `git` subprocess)
- Incoming HTTP requests (FastAPI API layer)
- SSO/OIDC HTTP calls (covered by `sso-authentication.md`)

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

These open points have answers that are largely dictated by standard
HTTP best practices. They are grouped here for efficient review — each
includes a "Deduction" section summarizing why the recommended option
is nearly certain, so the final decision can be made quickly.

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

**C) Composed User-Agent (base + fetcher name, automatic)**

Format: `Sentinel/{version} ({fetcher.name})`

The base string is centralized. The fetcher name comes from the
`name` attribute already required by the `BaseFetcher` contract.
Composition is automatic — fetcher authors do nothing.

- Pro: combines global identity with per-fetcher granularity at zero
  effort for authors; the `name` attribute already exists and is
  unique per fetcher
- Con: reveals internal fetcher names to external providers (minimal
  risk — names are generic and descriptive)

**D) No dedicated User-Agent (library default)**

- Pro: zero effort
- Con: no identification; NVD recommends identifying applications
  for favorable rate limits; unprofessional for enterprise software

**Recommendation**: Option C.

**Deduction**: the `name` attribute is already mandatory and unique per
fetcher (BaseFetcher contract). httpx allows setting `User-Agent` at
client instantiation level — zero per-fetcher effort. Exposing generic
descriptive names like `sync_nvd_cves` to external providers carries no
security risk. NVD documentation explicitly recommends application
identification for favorable rate limits. Option C is the only option
that provides both global identity and per-fetcher granularity with zero
author effort.

**Status**: `open`

**Resolution**: —

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

**Deduction**: httpx enables `Accept-Encoding: gzip, deflate, br` by
default when the `brotli` or `zstandard` codec is available (gzip/deflate
always available via stdlib). The "decision" here is to **document** this
explicitly as a default behavior of the shared client, not to implement
it — httpx already does it. Decompression CPU cost is negligible compared
to network I/O savings, especially for NVD responses (large JSON pages)
and IBS XML downloads. Documenting the default prevents future confusion
about whether compression is active.

**Status**: `open`

**Resolution**: —

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
| `sync_epss_scores` | Not specified | CSV |
| IBS fetchers (via `IBSClient`) | Not specified | XML |
| SMELT/AIMAAS fetchers | Not specified | JSON |

**Options**:

**A) Default Accept in shared client, per-fetcher override**

The shared client sets `Accept: application/json` by default. XML
consumers (IBS) and CSV consumers (EPSS) override to
`application/xml` and `text/csv` respectively. Service-specific
Accept values (GitHub) override as they already do.

- Pro: explicit content negotiation; fails early if a server returns
  an unexpected format
- Con: minor — adds a header that most servers ignore anyway

**B) No default — per-fetcher only when required by the API**

Only specify `Accept` when the target API requires it (as GHSA does).

- Pro: minimal spec changes
- Con: inconsistent; not all fetchers negotiate content type

**Recommendation**: Option A.

**Deduction**: 10 of 13 HTTP fetchers consume JSON. Setting `Accept:
application/json` as default covers the majority with zero per-fetcher
effort. The 3 non-JSON consumers (IBS → XML, EPSS → CSV, IBS product
release → binary/XML) override explicitly — this makes their non-standard
content expectation visible in the code. An explicit Accept header is a
standard HTTP best practice: it signals intent to the server and enables
clear error responses (406 Not Acceptable) when there is a content-type
mismatch, making debugging easier. The override mechanism is trivial
(pass `headers={"Accept": "..."}` or set it on a per-client instance).

**Status**: `open`

**Resolution**: —

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

**Deduction**: no fetcher currently demonstrates a performance problem
caused by redundant downloads. The only high-benefit case (IBS product
release detection) already tracks this as its own spec-level open item
(`ibs-product-release-detection.md:288-290`) — the solution will be
designed in that context where the specific requirements (storage
mechanism, invalidation strategy) are clearer. The shared client (OP-2)
should not preclude future conditional request support (i.e., do not
strip ETag/Last-Modified from responses), but should not implement
storage or opt-in mechanisms now. When the IBS case is resolved, it may
inform a reusable pattern for KEV and EPSS — but that is speculative
today.

**Status**: `open`

**Resolution**: —

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

**Recommendation**: Option B for v1.

**Deduction**: the decision is already made and documented in two
approved specifications (`fetcher-infrastructure.md`, `cve-sync-nvd.md`).
The fixed backoff of 5s + 10s + 20s = 35s total covers the known
rate-limit windows of current providers (NVD: 30s window with API key,
60s without). No provider in the current inventory has demonstrated a
rate-limit window that consistently defeats the fixed backoff. If one
is identified in the future, the fix is localized (add Retry-After
support to the transport retry for that specific case). Reopening this
decision now provides no concrete benefit.

**Status**: `open`

**Resolution**: —

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

**Status**: `open`

**Resolution**: —

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
| `detect_ibs_product_releases` | Read timeout → 120s+ | Downloads `updateinfo.xml` files that can be several MB for large product repositories |
| `sync_epss_scores` | Read timeout → 60s | Daily CSV download (~30MB compressed) |
| `sync_nvd_cves` (full sync) | Read timeout → 60s | Initial full sync pages with 2000 CVEs can be large |

**Timeout hierarchy (clarification)**:

```
┌─────────────────────────────────────────────────────┐
│ FetcherConfig.timeout_seconds (default: 3600s)      │  ← Celery task level
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

**Status**: `open`

**Resolution**: —

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
and connection errors only; NOT 429 by default since 429 handling
varies per provider). Fetchers with specific needs override or
disable the transport retry. This is complementary to, not a
replacement for, Celery task retry which operates at a different
scope.

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
│  │ Trigger: connection error, 502, 503, 504, timeout │  │
│  │ Policy: 3x with 1s/2s/4s backoff (proposed)      │  │
│  └───────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

**Worst case for `fetch_single()`** (single-request fetchers like KEV,
or on-demand single-CVE fetches):

- Transport retry: 3 attempts × 1 request = 3 HTTP requests
- If all 3 fail → exception propagates → Celery retries the task
- Celery retry: 3 task retries × 3 transport attempts = **9 total
  HTTP requests** over ~42s (transport: 1+2+4=7s × 3 Celery retries
  with 5+10+20=35s between them)

This is acceptable: 9 requests over 42 seconds for a persistently
failing service is not aggressive, and the total time (42s) is well
within the Celery task timeout (3600s default).

**For `execute()`** (batch fetchers — NVD, Red Hat, GHSA, etc.):

- No Celery task-level retry exists for `execute()` — if the task
  fails, it waits for the next scheduled run
- Transport retry operates independently per request within the batch
- Worst case per request: 3 attempts (same as `fetch_single`)
- Total for a batch of N requests with persistent failure: 3N requests
  before the fetcher's error-handling logic (abort or skip-and-continue)
  takes effect

**No amplification risk for `execute()`**: since there is no Celery
retry at the task level, transport retry only adds resilience against
transient blips (connection reset between pages, momentary 503) without
compounding.

**429 exclusion rationale**: HTTP 429 is excluded from transport-level
retry because:

1. Rate-limit policies vary per provider (NVD: 30s window; GitHub:
   per-hour budget; Red Hat: undocumented)
2. The correct response to 429 depends on context — some fetchers
   want to wait and retry (NVD), others want to skip and continue
   (Red Hat), others want to abort (GHSA)
3. NVD already specifies its own inline 429 retry; adding transport-
   level 429 retry would create conflicting behavior
4. 429 typically indicates a systemic problem (too many requests)
   that won't resolve in 1-4 seconds of backoff — unlike 502/503/504
   which often indicate momentary upstream issues

Fetchers that want 429 retry can enable it explicitly on their client
instance.

**Status**: `open`

**Resolution**: —

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

**Status**: `open` (blocked on factual verification)

**Resolution**: —

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

**Status**: `open`

**Resolution**: —

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

For SUSE internal services (`*.suse.de`), operators in environments
with a proxy will likely need to add these hosts to `NO_PROXY` since
they are reachable directly from the internal network. This should be
documented as a deployment note.

**Status**: `open`

**Resolution**: —

---

## Application Plan

This section maps each open point to the specifications that need
updating once the point is resolved. The plan is updated incrementally
as decisions are made.

### Specs affected by open point

| Spec file | OP-1 | OP-2 | OP-3 | OP-4 | OP-5 | OP-6 | OP-7 | OP-8 | OP-9 | OP-10 | OP-11 |
|-----------|------|------|------|------|------|------|------|------|------|-------|-------|
| `platform/fetcher-infrastructure.md` | Y | Y | Y | Y | Y | — | Y | — | — | Y | Y |
| `integrations/ibs-integration.md` | — | Y | — | — | — | Y | Y | — | — | — | — |
| `tickets/cve-sync-nvd.md` | — | — | Y | Y | — | — | — | — | — | — | — |
| `tickets/cve-sync-redhat.md` | — | — | — | Y | — | — | — | — | — | — | — |
| `tickets/cve-sync-ghsa.md` | — | — | — | Y | — | — | — | — | — | — | — |
| `tickets/cve-sync-osv.md` | — | — | — | Y | — | — | — | — | — | — | — |
| `tickets/cve-sync-kev.md` | — | — | Y | — | — | — | — | Y | — | — | — |
| `tickets/cve-sync-epss.md` | — | — | — | — | — | — | Y | Y | — | — | — |
| `packages/product-catalog.md` | — | — | — | — | — | Y | — | — | — | — | — |
| `packages/ibs-track-release-detection.md` | — | — | — | — | — | — | — | — | — | — | — |
| `packages/ibs-product-release-detection.md` | — | — | — | — | — | — | — | Y | — | — | — |
| `packages/package-bugowner.md` | — | — | — | — | — | — | — | — | — | Y | — |
| `packages/ibs-submission-tracking.md` | — | — | — | — | — | — | — | — | — | — | — |
| `docs/configuration.md` | — | — | Y | — | — | Y | — | — | — | — | Y |

All spec paths are relative to `docs/features/` unless otherwise noted.

### Detailed application instructions per open point

#### OP-1: User-Agent header

**Primary target**: `platform/fetcher-infrastructure.md`

- Add a "HTTP Client Defaults" section (or similar) to the
  `BaseFetcher` specification documenting:
  - The User-Agent format chosen
  - How it is composed (automatic from `name` attribute, version
    from application configuration)
  - That fetchers inherit it automatically
  - Override mechanism (if any)
- Individual fetcher specs do NOT need updating — the User-Agent is
  inherited from the base class, not declared per-fetcher

**Secondary targets**: none initially. If a fetcher requires a
non-standard User-Agent (unlikely), its spec would document the
override.

#### OP-2: Shared HTTP client factory

**Primary target**: `platform/fetcher-infrastructure.md`

- Extend the `BaseFetcher` contract with the client factory method
  or property (signature, return type, default configuration)
- Document the relationship between the fetcher-level client and
  any standalone factory (if Option A+B hybrid is chosen)
- Add the factory to the "What BaseFetcher provides" list (items
  1-4 in the current spec)
- Document client lifecycle (creation, pooling, teardown)

**Secondary target**: `integrations/ibs-integration.md`

- If `IBSClient` uses the shared factory, document this dependency
- If `IBSClient` remains independent, document why (different
  lifecycle, shared across multiple consumers)

#### OP-3: Default HTTP request timeout

**Primary target**: `platform/fetcher-infrastructure.md`

- Document the default timeout values (connect, read) in the HTTP
  client defaults section (see OP-1/OP-2)
- Clarify the distinction between HTTP request timeout (per-request)
  and `FetcherConfig.timeout_seconds` (Celery task-level, stale run
  detection)

**Per-fetcher updates**:
- `cve-sync-kev.md`: already specifies 30s — verify compatibility
  with the chosen default. If the default is 30s, the per-fetcher
  specification can reference the default instead of hardcoding
- `cve-sync-nvd.md`: may need explicit timeout for large paginated
  responses if default differs from NVD's needs

**Configuration**: `docs/configuration.md`
- If the default timeout is configurable via env var, add it to the
  configuration reference

#### OP-4: HTTP-level retry for transient errors

**Primary target**: `platform/fetcher-infrastructure.md`

- Document the transport-level retry policy (conditions, max
  attempts, backoff) as part of the HTTP client defaults
- Clarify the boundary between transport-level retry (automatic,
  per-request) and Celery task-level retry (per-task, for
  `fetch_single`)
- Document the retry amplification bounds

**Per-fetcher updates** (only where current behavior conflicts):
- `cve-sync-nvd.md`: NVD's inline 429 retry logic may overlap with
  transport-level retry. Document the interaction — e.g., transport
  retry handles 5xx/connection errors; NVD's inline logic handles
  429 specifically (since 429 is excluded from transport retry per
  recommendation)
- `cve-sync-ghsa.md`: GHSA aborts on any page failure. If transport
  retry is active, a transient 5xx would be retried transparently
  before reaching the abort logic. Document that this is the
  intended behavior
- `cve-sync-redhat.md`: Red Hat's `record_failed` + continue
  pattern operates after transport retry is exhausted. No conflict
- `cve-sync-osv.md`: similar to Red Hat — skip-and-continue after
  retry exhaustion. No conflict

#### OP-5: HTTP response compression

**Primary target**: `platform/fetcher-infrastructure.md`

- Add to the HTTP client defaults section: "The HTTP client sends
  `Accept-Encoding: gzip, deflate` by default. Responses are
  decompressed transparently."
- No per-fetcher updates needed

#### OP-6: TLS for SUSE internal services

Depends on factual determination of certificate chains.

**Potential targets**:
- `platform/fetcher-infrastructure.md`: if a shared CA bundle
  config is added
- `integrations/ibs-integration.md`: if IBS-specific TLS config
  is needed
- `packages/product-catalog.md`: if SMELT/AIMAAS need TLS config
- `docs/configuration.md`: new env var for CA bundle path

#### OP-7: Default Accept header

**Primary target**: `platform/fetcher-infrastructure.md`

- Document the default `Accept: application/json` in HTTP client
  defaults

**Per-fetcher documentation** (only non-JSON consumers):
- `integrations/ibs-integration.md` (`IBSClient`): document
  `Accept: application/xml`
- `tickets/cve-sync-epss.md`: document `Accept: text/csv`
  (when EPSS spec is written)

#### OP-8: Conditional HTTP requests

Deferred (recommended). If resolved as Option C:
- No spec changes needed
- `ibs-product-release-detection.md:288-290` retains its existing
  open item independently

If resolved as Option A:
- `platform/fetcher-infrastructure.md`: document the utility methods
- Per-fetcher specs for KEV, EPSS, IBS product release: document
  opt-in usage

#### OP-9: Retry-After handling

If resolved as Option B (keep ignoring):
- No spec changes needed — already documented

If reconsidered later:
- `platform/fetcher-infrastructure.md:433-434`: update the
  deliberate-ignore note
- `cve-sync-nvd.md:225`: update the corresponding note

#### OP-10: Rate limiting pattern

**Primary target**: `platform/fetcher-infrastructure.md`

- Define `FetcherConfig.rate_limit` semantics (unit, type, behavior)
- Document enforcement responsibility (client vs. fetcher)
- Document the relationship with per-fetcher custom settings

**Per-fetcher updates**:
- `packages/package-bugowner.md`: already references `rate_limit`
  from `FetcherConfig` — verify consistency with new semantics
- `tickets/cve-sync-nvd.md`: document relationship between generic
  `rate_limit` and `request_delay_seconds` custom setting
- `tickets/cve-sync-redhat.md`: document relationship between
  generic `rate_limit` and `throttle_delay_seconds` custom setting

#### OP-11: HTTP proxy support

**Primary target**: `platform/fetcher-infrastructure.md`

- Add a "Proxy Configuration" paragraph to the HTTP client defaults
  section documenting that standard env vars are respected

**Secondary target**: `docs/configuration.md`

- Add `HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY` to the environment
  variables index with a note that they are standard (not
  Sentinel-specific) and apply to all outgoing HTTP connections
- Add deployment note about `NO_PROXY` for `*.suse.de` in proxy
  environments

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
