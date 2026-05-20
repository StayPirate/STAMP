# Open Points

Architectural decisions pending resolution before implementation begins.

---

## 1. Enum Storage Strategy: PostgreSQL ENUM vs VARCHAR + Python Enum

**Origin**: while fixing finding UMGT-API-02 (missing validation for
invalid role values in `POST /api/v1/admin/users/{user}/roles`), we
identified that the choice of enum storage strategy has broad
implications across the project.

**Context**: the project currently specifies (in `docs/data-model.md`,
line 868) that all ENUM types are PostgreSQL enums. However, 4 of the
12 enums in the system are "evolving" — their value sets grow as new
features are added:

| Enum | Current values | Growth driver |
|------|---------------|---------------|
| Role | 2 | New roles as platform matures |
| TicketAuditEventType | 24 | Every new ticket mutation type |
| CVESourceType | 2 | New data source integrations |
| FetcherAuditEventType | 4 | New admin operations on fetchers |

With PostgreSQL ENUM, adding a value requires an Alembic migration
(`ALTER TYPE ... ADD VALUE`) that must run before the new application
code is deployed. Removing a value is even more complex (requires
recreating the type). This creates deployment coupling and operational
risk for values that are expected to change.

**Proposed alternative**: a hybrid approach where stable enums (8) keep
PostgreSQL ENUM for database-level integrity, while evolving enums (4)
use VARCHAR columns with validation enforced exclusively through a
Python Enum (single source of truth in `app/core/enums.py`). Adding a
value to an evolving enum would require only a code change — no
migration.

**Why it matters before implementation**: the storage type chosen for
these columns affects model definitions, Alembic migrations, deployment
procedures, and the testing strategy. Changing this after models are
implemented would require a non-trivial migration to convert existing
PostgreSQL ENUM columns to VARCHAR.

**Decision needed**: hybrid (stable=PG ENUM, evolving=VARCHAR) vs full
VARCHAR for uniformity. See conversation for detailed tradeoff analysis.

---

## 2. Rate Limiting via Dedicated Reverse Proxy

**Origin**: SSO-SEC-02 finding during sso-authentication spec review.

**Context**: the SSO endpoints (`POST /api/v1/auth/sso/callback` and
`GET /api/v1/auth/sso/authorize`) are public and perform
cryptographic operations (HMAC verification) and outbound HTTP requests
(token exchange with IdP) on every call. Without rate limiting, an
attacker could flood these endpoints for DoS against Sentinel or to
trigger rate limiting at the IdP, blocking legitimate logins.

More broadly, rate limiting is a cross-cutting concern that applies to
multiple public endpoints (login, SSO, password reset, etc.), not just
SSO.

**Proposed approach**: deploy a dedicated reverse proxy (nginx, Traefik,
or Kubernetes ingress controller) in front of Sentinel with rate
limiting rules per endpoint. This is preferable to application-level
rate limiting because:

- Centralized configuration — applies consistently across all endpoints
- More efficient — requests are rejected before reaching the application
- Avoids per-request Redis dependency for rate limit state
- Aligns with Sentinel's architecture (nginx already planned for
  frontend/API routing)

**Recommended limits** (starting point):

| Endpoint | Limit | Window |
|----------|-------|--------|
| `GET /api/v1/auth/sso/authorize` | 20 requests per IP | 1 minute |
| `POST /api/v1/auth/sso/callback` | 10 requests per IP | 1 minute |
| `POST /api/v1/auth/login` | 10 requests per IP | 1 minute |

**When to implement**: before staging/production deployment. Not needed
for local development.

**Decision needed**: which proxy to use (nginx is already in the stack
for frontend serving — may be sufficient), and whether to add
application-level rate limiting as defense-in-depth or rely solely on
the proxy.

---

## 3. Orphan CVE Re-Ticketing Mechanism

**Origin**: spec inconsistency found while reviewing the CVE-Ticket
cardinality in the ER diagram.

**Context**: `docs/features/tickets/tickets.md` (lines 125-129) states
that after an Admin dissociates a CVE from a ticket, "a subsequent CVE
sync will create a new ticket for it". However,
`docs/features/tickets/cve-tracking.md` (line 229) specifies that the
sync fetchers (`sync_cves_nvd`, `sync_cves_mitre`) create tickets only
for **newly ingested** CVEs — i.e., CVEs that do not yet exist in the
database. For CVEs that already exist, the sync only updates data
(references, scores, etc.) without checking whether a ticket is
associated.

This means that after a CVE dissociation, the CVE remains in the
database without a ticket indefinitely. The only partial safety net is
IBS Case C (`ibs-track-release-detection.md`, lines 153-176), which
creates a ticket when a CVE fix is found in an IBS diff — but this is
reactive and narrow, not a general orphan scan.

**Options**:

- **(A) Extend sync fetchers**: when processing an existing CVE, check
  whether a ticket exists for it. If not, create one. This is the
  simplest fix and fulfills the promise in `tickets.md`, but adds a
  query per existing CVE on every sync run.
- **(B) Remove the re-ticketing claim**: update `tickets.md` to state
  that dissociation leaves the CVE without a ticket, and that the Admin
  is responsible for re-associating it before the next sync. Simpler,
  but orphan CVEs become a silent operational risk.
- **(C) Dedicated periodic task**: create a lightweight "orphan CVE
  scanner" that runs periodically (e.g., daily) and creates tickets for
  any CVE without one. Decouples the concern from the sync fetchers.

**Decision needed**: which approach to adopt for resolving the
inconsistency between `tickets.md` and `cve-tracking.md`.

---

## 4. Remove Protected State Behavior from WONT_FIX

**Origin**: analysis of `PackageStatus` enum semantics and how
final/non-final classification is used across feature specifications.

**Context**: `WONT_FIX` is currently treated as a "protected state" —
immune to all automatic/system-initiated status transitions. This means:

- Track-level release detection skips `WONT_FIX` tracks (does not
  transition them to `FIXED` even if a fix is detected in IBS)
- Product status propagation from track to products skips `WONT_FIX`
  products (even without `is_status_override`)
- Product-level release detection excludes `WONT_FIX` products from
  scanning entirely
- CVSS eligibility recalculation excludes `WONT_FIX` products
- EOL lifecycle transitions do not modify `WONT_FIX` products

This distinction is not justified. `WONT_FIX` should behave like the
other two final statuses (`FIXED`, `NOT_AFFECTED`) with respect to
automatic transitions.

**Specifications that reference the protected state concept** (all will
need updating):

- `docs/features/packages/package-model.md` (lines 592-596, 1016,
  1045) — authoritative definition and workflow-agnostic rule
- `docs/features/packages/ibs-track-release-detection.md` (lines 34-36,
  130)
- `docs/features/packages/ibs-product-release-detection.md` (lines
  30-32, 236-237, 259-261)
- `docs/features/integrations/ibs-rabbitmq-integration.md` (line 182)
- `docs/features/integrations/ibs-integration.md` (lines 243-244)
- `docs/features/tickets/cvss-scoring.md` (line 347)
- `docs/features/packages/product-lifecycle-transitions.md` (lines 98,
  164-168)
- `docs/architecture.md` (lines 264-265)

**Decision needed**: define the new behavior for `WONT_FIX` under
automatic transitions. Options:

- **(A)** Treat `WONT_FIX` exactly like `FIXED` — automatic transitions
  apply identically (release detection can transition it, propagation
  overwrites it unless `is_status_override` is set)
- **(B)** Treat `WONT_FIX` exactly like `NOT_AFFECTED` — automatic
  transitions do not target it (not in the source set for release
  detection) but propagation can overwrite it

In both cases, the "Protected state" section in `package-model.md`
and all references to it across the codebase would be removed.
