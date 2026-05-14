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
| TicketAuditEventType | 21 | Every new ticket mutation type |
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
