# Review: networking

**Spec**: `docs/features/platform/networking.md`
**Last reviewed**: 2026-07-02
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions, Documentation

> Post-split review (fetcher-infrastructure split, Phase 4m). The document
> was created by extracting the shared HTTP client + TLS trust store
> content from the former monolithic `fetcher-infrastructure.md`. The split
> was content-preserving; the gap findings below are pre-existing
> ambiguities, not regressions introduced by the split, and are recorded
> here for future hardening rather than fixed as part of the split exercise.

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

**Status**: RESOLVED — HTTP-date ≤ now() treated as absent; clock skew addressed (2026-06-27)

### NET-GAP-08 — `name` parameter handling for non-fetcher callers unspecified (Low)

**Status**: RESOLVED — Factory signature changed to require name as formal parameter (2026-06-27)

### NET-GAP-09 — Valid-but-wrong CA file (silent failure) not covered (Low)

**Status**: RESOLVED — Auto-resolved: runtime TLS handshake errors provide sufficient diagnostic; no startup validation needed (2026-06-27)

### NET-GAP-10 — Which error propagates on retry exhaustion is unspecified (Low)

**Status**: RESOLVED — Auto-resolved: behavior is implicit (last-error-wins); no consumer relies on this distinction (2026-06-27)

---

## Coherence

No issues identified.

---

## Design

### NET-DES-01 — Cert file existence check in readiness probe (Medium)

**Category**: Operational Resilience
**Status**: OPEN

If a deployment ships without the SUSE CA certificate file (e.g., Dockerfile COPY directive accidentally removed during refactoring), the application starts normally, passes the `/health` liveness check, and begins receiving traffic. However, all fetchers connecting to SUSE internal services (IBS, SMELT, AIMAAS) and the IBSEventConsumer (RabbitMQ over AMQPS) fail with TLS verification errors. This could persist for hours until someone notices the fetcher dashboard showing all-failures. The `/ready` readiness endpoint should validate that `SUSE_CA_CERT_PATH` exists and is parseable as a valid PEM certificate. This would cause the orchestrator to reject the deployment immediately, surfacing the problem at deploy time rather than at runtime.

---

## Security

### NET-SEC-01 — Explicit follow_redirects=False as factory default (Medium)

**Category**: Credential Protection
**Status**: OPEN

The spec does not define a redirect-following policy for the HTTP client factory. The current behavior relies on httpx's default of not following redirects, which is safe but implicit. If a future contributor enables `follow_redirects=True` (e.g., because NVD or GitHub returns 301/302), authenticated requests carrying IBS HTTP Basic Auth credentials, NVD API keys, or GitHub tokens in the Authorization header would be forwarded to redirect targets — potentially leaking credentials to arbitrary servers. Making `follow_redirects=False` an explicit, documented factory parameter ensures this security property survives library version upgrades and code modifications. If specific consumers need redirect following in the future, they should opt in explicitly with credential-stripping on cross-origin redirects.

### NET-SEC-02 — Response body size limit (max_content_length) (Medium)

**Category**: Resource Exhaustion
**Status**: OPEN

The spec defines timeouts (10s connect, 30s read) but no maximum response body size. A malicious, compromised, or malfunctioning external service could send an unbounded response body (multi-GB), exhausting worker memory and causing an OOM kill. This affects all fetchers, especially those connecting to public services (NVD, GitHub, MITRE, OSV, Red Hat) where Sentinel does not control the server. The factory defaults table should include a `max_content_length` parameter (e.g., 100 MB as a generous default). The implementation should abort the response read if the Content-Length header exceeds this limit, or track bytes read during streaming and abort if the threshold is crossed. Individual fetchers can override this default downward for endpoints with known small responses.

---

## API Conventions

No API endpoints defined in this spec.

---

## Documentation

### NET-DOC-01 — Dangling reference to the moved "Shared HTTP Client" section (Medium)

**Status**: RESOLVED — `fetcher-infrastructure.md` (BaseFetcher Base Class, item 5) pointed to a "Shared HTTP Client" section that no longer exists in that document after the split. Updated to reference the local "BaseFetcher HTTP Client Integration" section and `networking.md` ("Shared HTTP Client") for the full factory spec (2026-06-25)

### NET-DOC-02 — Factory signature omits the required `name` parameter (Low)

**Status**: RESOLVED — Factory signature now declares name: str as required parameter (2026-06-27)
