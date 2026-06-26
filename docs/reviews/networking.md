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

**Status**: RESOLVED — Method safety sub-section added to Transport-Level Retry; retry restricted to idempotent methods by default with opt-in for non-idempotent; httpx built-in retry explicitly excluded; IBSClient retry safety documented in ibs-integration.md (2026-06-26)

### NET-GAP-02 — Timing of the missing-CA-file warning is ambiguous (Medium)

**Status**: RESOLVED — SSL context lifecycle clarified: built fresh per create_http_client() invocation, not at startup; warning frequency documented per component type (2026-06-26)

### NET-GAP-03 — Corrupt-CA handling for non-HTTP protocols unspecified (Medium)

**Status**: RESOLVED — Introduced shared `build_tls_context()` function with uniform contract (missing→warning, corrupt→TLSConfigurationError); documented component-specific error handling in ibs-rabbitmq-integration.md (terminate process) and ad-integration.md (fail without retry) (2026-06-26)

### NET-GAP-04 — Connection pool size limits unspecified (Medium)

**Status**: RESOLVED — Explicit pool limits (100 max connections, 20 max keepalive) added to Default Configuration table with override mechanism; note clarifying sequential usage pattern added; pool override example added to fetcher-infrastructure.md Override Mechanism section (2026-06-26)

### NET-GAP-05 — TLS verification override safety not enforced (Medium)

**Status**: RESOLVED — TLS verify documented as overridable with WARNING log; UA remains template-protected (2026-06-26)

### NET-GAP-06 — SSL context caching / certificate rotation behavior unspecified (Medium)

**Status**: RESOLVED — Explicitly specified that SSL context is rebuilt per create_http_client() invocation (no module-level caching); fetchers pick up rotated CA on next run without restart (2026-06-26)

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
