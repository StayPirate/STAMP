# Review: networking

**Spec**: `docs/features/platform/networking.md`
**Last reviewed**: 2026-06-25
**Reviewers**: Gap Analysis, Documentation

> Post-split review (fetcher-infrastructure split, Phase 4m). The document
> was created by extracting the shared HTTP client + TLS trust store
> content from the former monolithic `fetcher-infrastructure.md`. The split
> was content-preserving; the gap findings below are pre-existing
> ambiguities, not regressions introduced by the split, and are recorded
> here for future hardening rather than fixed as part of the split exercise.
> Coherence, Design, Security and API Conventions reviewers were not run in
> this round.

---

## Gap Analysis

### NET-GAP-01 — Transport retry on non-idempotent requests may duplicate writes (High)

**Category**: Error/concurrency (retry idempotency)
**Status**: OPEN

Transport-level retry treats all timeouts (including read/write timeouts)
and connection errors as retryable, without distinguishing idempotent
(GET) from non-idempotent (POST/PUT) requests. A read/write timeout can
occur after the server has received and begun processing a request. The
shared client is used by `IBSClient`, which performs write operations.
Automatically retrying a timed-out POST could submit a duplicate
operation. The spec should either restrict timeout/connection retry to
idempotent methods or explicitly state that all consumers must ensure
idempotency.

### NET-GAP-02 — Timing of the missing-CA-file warning is ambiguous (Medium)

**Category**: TLS failure handling
**Status**: OPEN

The spec states a warning is emitted "at startup" for a missing CA file,
but also that the SSL context is built "at client creation time". For
`BaseFetcher`, `self.http_client` is a lazy property created during
`execute()`, not at startup. It is unspecified when the missing-file
check runs and whether the warning fires once at process start, once per
client creation (per fetcher run), or per request.

### NET-GAP-03 — Corrupt-CA handling for non-HTTP protocols unspecified (Medium)

**Category**: TLS failure handling
**Status**: OPEN

The corrupt/unparseable CA file behavior is documented only for the
HTTP/fetcher path ("the fetcher fails"). The same `SUSE_CA_CERT_PATH`
feeds the LDAP (`sync_ldap_directory`) and AMQP (`IBSEventConsumer`) SSL
contexts. The behavior of the long-lived `IBSEventConsumer` on a corrupt
CA file (crash at startup, crash-loop, retry) is unspecified, despite the
consumer being a continuously-running process where this matters most.

### NET-GAP-04 — Connection pool size limits unspecified (Medium)

**Category**: Concurrency
**Status**: OPEN

The spec defines a Pool timeout (10s) but never specifies the connection
pool size limits (max connections, max keepalive). A fetcher issuing many
parallel requests (up to the documented `max_concurrent_requests`, e.g.
50) against the default httpx pool would experience pool-timeout errors
once the pool is saturated. For a shared HTTP client spec, the pool
capacity is a core concurrency parameter; its absence forces an
implementer to guess (httpx defaults vs. explicit limits).

### NET-GAP-05 — TLS verification override safety not enforced (Medium)

**Category**: Override safety / TLS enforcement
**Status**: OPEN

The spec states TLS verification is "always enforced", but the factory
signature is `create_http_client(**overrides)` with last-writer-wins
semantics, and only User-Agent is documented as protected. It is
unspecified whether `verify=False` (or a substitute SSL context) can be
injected via `http_client_options`/overrides, which would silently defeat
the "always enforced" guarantee. The spec should mark `verify`/TLS as
non-overridable like User-Agent.

### NET-GAP-06 — SSL context caching / certificate rotation behavior unspecified (Medium)

**Category**: Certificate rotation
**Status**: OPEN

The spec says the SSL context is built at client creation time and that
long-lived clients need a restart to pick up a rotated CA. It is
unspecified whether the combined trust store / SSL context is cached at
module level or rebuilt on every `create_http_client()` call. This
determines whether fetchers (which create a fresh client per run) pick up
a rotated CA on the next run without restart, or whether even they require
a restart (if cached at import). The two interpretations yield materially
different operational behavior.

### NET-GAP-07 — Retry-After HTTP-date in the past / clock skew not addressed (Low)

**Category**: Boundary condition
**Status**: OPEN

Retry-After parsing handles negative integers as absent, but does not
address an HTTP-date that resolves to a past instant (server clock skew,
or an already-elapsed date). The computed wait would be ≤ 0; it is
unspecified whether this is treated as absent, clamped to 0, or compared
against the 120s ceiling. The `≤ 120s` threshold for HTTP-dates depends on
`date - now()`, which is clock-skew sensitive and not discussed.

### NET-GAP-08 — `name` parameter handling for non-fetcher callers unspecified (Low)

**Category**: Function completeness
**Status**: OPEN

The User-Agent format requires `{name}`, and the spec says "the `name`
parameter is passed explicitly to the factory", but the factory signature
(`create_http_client(**overrides)`) does not declare `name` as a formal
required parameter. The behavior when a non-fetcher caller omits `name`
(raise, empty segment, default value) is unspecified. (See also
NET-DOC-02.)

### NET-GAP-09 — Valid-but-wrong CA file (silent failure) not covered (Low)

**Category**: Boundary condition
**Status**: OPEN

The spec covers missing and corrupt CA files, but not a file that is valid
PEM yet does not contain the SUSE CA (e.g., wrong cert, empty-but-valid
file). This parses successfully (no startup warning, no creation error)
but produces runtime TLS handshake failures on every SUSE-internal
connection — the silent-failure mode between the two documented cases.

### NET-GAP-10 — Which error propagates on retry exhaustion is unspecified (Low)

**Category**: Error path
**Status**: OPEN

When all attempts fail with heterogeneous errors across the sequence
(e.g., timeout, then 503, then connection error), it is unspecified which
exception is surfaced (last, first, or aggregate). This affects the
sanitized message the fetcher can produce.

---

## Documentation

### NET-DOC-01 — Dangling reference to the moved "Shared HTTP Client" section (Medium)

**Status**: RESOLVED — `fetcher-infrastructure.md` (BaseFetcher Base Class,
item 5) pointed to a "Shared HTTP Client" section that no longer exists in
that document after the split. Updated to reference the local "BaseFetcher
HTTP Client Integration" section and `networking.md` ("Shared HTTP
Client") for the full factory spec (2026-06-25)

### NET-DOC-02 — Factory signature omits the required `name` parameter (Low)

**Status**: OPEN

The factory is documented as `create_http_client(**overrides)`, but `name`
is mandatory to build the User-Agent and is passed explicitly by callers.
The signature/docstring does not surface `name` as a distinct required
argument, leaving a small ambiguity about how a non-overridable value is
supplied through `**overrides`. Pre-existing (content-preserving split);
overlaps with NET-GAP-08.
