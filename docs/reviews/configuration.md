# Review: configuration

**Spec**: `docs/configuration.md`
**Last reviewed**: 2026-07-28
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### CFG-GAP-01 — Notes for Operators #3 overstates startup validation uniformity (Medium)

**Category**: Configuration and defaults
**Status**: OPEN

Notes for Operators #3 states: "Startup validation: the application validates all required settings at boot and fails fast with a clear error message indicating which variable is missing or invalid." This is factually incorrect for two settings:

- `LOGIN_MAX_ATTEMPTS`: values below 1 silently fall back to the default (5) with a startup warning instead of failing fast
- `LOGIN_LOCKOUT_MINUTES`: same behavior — values below 1 silently fall back to the default (10) with a startup warning

An operator reading Note #3 would expect `LOGIN_MAX_ATTEMPTS=0` to cause a startup failure. Instead, the application starts with the value silently reset to 5. This contradicts the "fails fast" claim and creates a false expectation of uniform validation behavior.

Either Note #3 should be qualified to exclude these settings, or the settings should be changed to fail-fast consistent with `JWT_EXPIRY_HOURS` (which does fail-fast on invalid values).

### CFG-GAP-02 — Empty string vs. unset not distinguished for SSO settings (Medium)

**Category**: Configuration and defaults
**Status**: OPEN

The SSO Configuration section states: "If any of the required SSO settings is missing, the application starts with SSO disabled." The term "missing" is ambiguous — it does not clarify whether it means the environment variable is unset or whether it also includes set-to-empty-string.

In Kubernetes deployments, ConfigMaps may inject empty strings for unset keys (e.g., `SSO_CLIENT_SECRET=""`). Pydantic's `Settings` class treats an empty string as a valid non-None value, so an implementer checking `if settings.sso_client_secret is not None` would consider it "present" while an implementer checking `if not settings.sso_client_secret` would consider it "missing." Two reasonable implementations would produce different behavior.

For comparison, `GITHUB_TOKEN` correctly distinguishes "empty or unset" in its description. The SSO settings should use the same precision.

---

## Coherence

### CFG-COH-01 — SMELT_API_URL / AIMAAS_API_URL circular authority (Medium)

**Category**: Configuration consistency
**Status**: OPEN

The SMELT / AIMAAS table lists `SMELT_API_URL` and `AIMAAS_API_URL` with "Defined in" pointing to `docs/features/packages/product-catalog.md`. However, product-catalog.md does not define these variables authoritatively — it mentions them inline without providing the full definition (name, type, default, bounds) required by the Configuration Management convention in `docs/conventions.md`.

This creates a circular reference: configuration.md defers authority to product-catalog.md, but product-catalog.md does not exercise that authority. Per the convention, the feature spec should "define semantics, name, type, default, bounds" as the source of truth.

Resolution options: either product-catalog.md should add an authoritative variable definition section for these two variables, or the "Defined in" column should show `—` (indicating configuration.md is the authority, consistent with how `DATABASE_URL`, `REDIS_URL`, and `CORS_ORIGINS` are handled).

---

## Design

### CFG-DES-01 — Inconsistent validation behavior for login rate-limiting settings (Medium)

**Status**: RESOLVED — Cross-agent duplicate of CFG-GAP-01 (2026-07-28)

---

## Security

### CFG-SEC-01 — CORS allow_methods and allow_headers are wildcard (Medium)

**Category**: CORS and HTTP Security
**Status**: OPEN

The Application table defines `CORS_ORIGINS` but is silent on `allow_methods` and `allow_headers`. The implementation uses `allow_methods=["*"]` and `allow_headers=["*"]`, which is overly permissive. Combined with `allow_credentials=True` (required for cookie-based sessions), wildcard methods and headers unnecessarily widen the attack surface.

The API only uses `GET`, `POST`, `PATCH`, and `DELETE` — there is no reason to allow `PUT`, `OPTIONS` (beyond preflight), `HEAD`, or `TRACE`. Similarly, only specific headers (`Authorization`, `Content-Type`, `X-Request-ID`) are needed.

Since CORS configuration is authoritatively defined in configuration.md (the "Defined in" column is `—`), the spec should document the restricted set of allowed methods and headers, or expose them as configurable environment variables with secure defaults.

### CFG-SEC-02 — IBS_RABBITMQ_URL default embeds credentials in spec (Medium)

**Category**: Secrets and Configuration
**Status**: OPEN

The IBS RabbitMQ Consumer table documents the default value of `IBS_RABBITMQ_URL` as `amqps://suse:suse@rabbit.suse.de`, embedding credentials (`suse:suse`) directly in the committed specification text.

Per `docs/conventions.md` (Secret Field Typing), credential-bearing URL fields should use `Field(..., repr=False)` in implementation, but the spec itself normalizes embedding credentials in documentation. Even if these are well-known defaults for the internal RabbitMQ bus, the pattern sets a precedent that conflicts with the project's security posture.

The default value should either omit credentials (e.g., `amqps://rabbit.suse.de` with a note that credentials must be provided), or use a placeholder (e.g., `amqps://<user>:<pass>@rabbit.suse.de`).

---

## API Conventions

