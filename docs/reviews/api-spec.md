# Review: api-spec

**Spec**: `docs/api-spec.md`
**Last reviewed**: 2026-07-21
**Reviewers**: Gap Analysis

---

## Gap Analysis

### APIS-GAP-01 — No validation/sanitization rule for client-supplied `X-Request-ID` (Medium)

**Category**: Missing input validation
**Status**: OPEN

The Request Tracing section states that the response header "contains
a UUID" and that the server "adopts" a client-sent value, but does not
specify any charset/length bounds, nor handling for malformed, empty,
or duplicate `X-Request-ID` headers. Without a bound, a client can
force an arbitrary value into the response header and into every log
line for that request — a log-injection vector when
`LOG_FORMAT=console` is used, and a contract violation of "contains a
UUID". Recommended resolution direction: adopt the client value only
if it matches a bounded charset/length (e.g., ≤128 chars,
`[A-Za-z0-9._-]`); otherwise generate a UUID; on duplicate headers, use
the first and ignore the rest.

### APIS-GAP-02 — Ambiguous scope of "end-to-end debugging" wording (Low)

**Category**: Ambiguous behavior
**Status**: OPEN

The wording "propagated to all log entries produced during request
processing, enabling end-to-end debugging" does not state whether
"end-to-end" extends into asynchronous work the request may enqueue
(Celery tasks). `docs/features/platform/logging.md` scopes correlation
IDs to their own execution unit, with no automatic propagation across
an `apply_async()` boundary. Recommend clarifying this section's
wording to match that scope explicitly (synchronous request-processing
lifecycle only), or revisiting the scope decision if broader
propagation is later deemed necessary.

---

## Coherence

_Not yet reviewed._

---

## Design

_Not yet reviewed._

---

## Security

_Not yet reviewed._

---

## API Conventions

_Not yet reviewed._
