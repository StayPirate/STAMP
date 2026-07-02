# Configuration Reference

Centralized index of all environment variables and runtime settings
required to deploy and operate Sentinel. Each setting is defined
authoritatively in the feature specification linked in the "Defined in"
column — this document is an aggregated reference, not the source of
truth.

## Required Secrets

These must be provided in every environment. The application refuses to
start if any is missing.

| Env Var | Type | Description | Defined in |
|---------|------|-------------|------------|
| `JWT_SECRET_KEY` | string (>=32 chars) | Symmetric key for signing JWTs | `docs/features/identity/authentication.md` |
| `DATABASE_URL` | string | PostgreSQL async connection string (e.g. `postgresql+asyncpg://user:pass@host:5432/db`) | `docs/architecture.md` |

## Required Connection Settings

These have sensible local-development defaults but must be configured
explicitly in staging/production.

| Env Var | Type | Default | Description | Defined in |
|---------|------|---------|-------------|------------|
| `REDIS_URL` | string | `redis://localhost:6379/0` | Redis URL for session cache and rate limiting | `docs/architecture.md` |
| `CELERY_BROKER_URL` | string | `redis://localhost:6379/1` | Celery task broker URL | `docs/architecture.md` |
| `CELERY_RESULT_BACKEND` | string | `redis://localhost:6379/2` | Celery result backend URL | `docs/architecture.md` |

## Celery Worker Configuration

These settings control the Celery worker and Beat scheduler behavior.
The timezone settings are **fixed** — they MUST NOT be overridden.

| Env Var | Type | Default | Description | Defined in |
|---------|------|---------|-------------|------------|
| `CELERY_TIMEZONE` | string | `UTC` | Timezone for Celery Beat cron interpretation. MUST remain `UTC` — all fetcher schedules are expressed in UTC. Overriding this value causes all scheduled fetchers to run at incorrect times | `docs/conventions.md` |
| `CELERY_ENABLE_UTC` | bool | `true` | Forces Celery internal message timestamps to UTC. MUST remain `true` | `docs/conventions.md` |

**Startup validation**: the application MUST validate at Celery worker
startup that these settings are `UTC` and `true` respectively. If either
is overridden to a non-UTC value, the worker MUST refuse to start and
log an error: `"FATAL: Celery timezone must be UTC. Current value:
{value}. All fetcher schedules assume UTC — see docs/conventions.md."`

## SSO Configuration

All SSO settings are **optional**. If any of the required SSO settings
(`SSO_ISSUER_URL`, `SSO_CLIENT_ID`, `SSO_CLIENT_SECRET`,
`SSO_REDIRECT_URI`) is missing, the application starts with **SSO
disabled**: the login page shows only the local credentials form (the
"Login with SUSE SSO" button is not rendered), and the SSO endpoints
(`/api/v1/auth/sso/authorize`, `/api/v1/auth/sso/callback`) return
HTTP 404.

At startup, the application logs an INFO message indicating SSO status:

- All SSO settings present: `"SSO authentication enabled
  (issuer: {SSO_ISSUER_URL})"`
- One or more settings missing: `"SSO authentication disabled — missing
  settings: {list of missing setting names}"` (secret values are never
  logged; only the setting names appear)

| Env Var | Type | Default | Description | Defined in |
|---------|------|---------|-------------|------------|
| `SSO_ISSUER_URL` | string | — | OIDC issuer URL (e.g. `https://id.suse.com`). Required for SSO. | `docs/features/identity/sso-authentication.md` |
| `SSO_CLIENT_ID` | string | — | OIDC client ID. Required for SSO. | `docs/features/identity/sso-authentication.md` |
| `SSO_CLIENT_SECRET` | string | — | OIDC client secret. Required for SSO. | `docs/features/identity/sso-authentication.md` |
| `SSO_REDIRECT_URI` | string | — | OAuth2 callback URL. Required for SSO. | `docs/features/identity/sso-authentication.md` |
| `SSO_USER_CLAIM` | string | `sub` | OIDC ID token claim used to identify the user (matched against `username` for AD-synced users). Only relevant when SSO is enabled. | `docs/features/identity/sso-authentication.md` |

## Authentication

| Env Var | Type | Default | Description | Defined in |
|---------|------|---------|-------------|------------|
| `JWT_EXPIRY_HOURS` | int | `72` | JWT token lifetime in hours (3 days). Tokens are refreshed transparently via sliding session for active users. Must be >= 1; values > 720 log a warning | `docs/features/identity/authentication.md` |
| `SESSION_MAX_LIFETIME_DAYS` | int | `30` | Maximum session lifetime in days. After this period from login, the session expires unconditionally regardless of activity. Must be >= 1; values > 365 log a warning | `docs/features/identity/authentication.md` |
| `LOGIN_MAX_ATTEMPTS` | int | `5` | Failed login attempts before account lockout. Must be >= 1 | `docs/features/identity/local-authentication.md` |
| `LOGIN_LOCKOUT_MINUTES` | int | `10` | Lockout duration in minutes. Must be >= 1 | `docs/features/identity/local-authentication.md` |


## IBS (Internal Build Service)

| Env Var | Type | Default | Description | Defined in |
|---------|------|---------|-------------|------------|
| `IBS_API_URL` | string | `https://api.suse.de` | IBS API base URL | `docs/features/integrations/ibs-integration.md` |
| `IBS_USERNAME` | string | — | IBS HTTP Basic Auth username | `docs/features/integrations/ibs-integration.md` |
| `IBS_PASSWORD` | string | — | IBS HTTP Basic Auth password | `docs/features/integrations/ibs-integration.md` |
| `IBS_DOWNLOAD_BASE_URL` | string | `https://download.suse.de/ibs` | HTTP download base for repository data | `docs/features/integrations/ibs-integration.md` |

## IBS RabbitMQ Consumer

| Env Var | Type | Default | Description | Defined in |
|---------|------|---------|-------------|------------|
| `IBS_RABBITMQ_URL` | string | `amqps://suse:suse@rabbit.suse.de` | AMQP broker URL | `docs/features/integrations/ibs-rabbitmq-integration.md` |
| `IBS_RABBITMQ_ENABLED` | bool | `true` | Enable/disable the RabbitMQ consumer process | `docs/features/integrations/ibs-rabbitmq-integration.md` |
| `IBS_RABBITMQ_ROUTING_KEYS` | string | `suse.obs.package.commit,suse.obs.request.create,suse.obs.request.state_change` | Comma-separated routing keys | `docs/features/integrations/ibs-rabbitmq-integration.md` |
| `IBS_RABBITMQ_RECONNECT_INITIAL` | int | `5` | Initial reconnect delay (seconds) | `docs/features/integrations/ibs-rabbitmq-integration.md` |
| `IBS_RABBITMQ_RECONNECT_MAX` | int | `300` | Maximum reconnect delay (seconds) | `docs/features/integrations/ibs-rabbitmq-integration.md` |

## LDAP Directory Sync

| Env Var | Type | Default | Description | Defined in |
|---------|------|---------|-------------|------------|
| `LDAP_URI` | string | `ldaps://pan.suse.de:636` | LDAP server URI. Must use `ldaps://` scheme — plaintext `ldap://` is not supported (see security rationale in spec) | `docs/features/identity/ad-integration.md` |
| `SUSE_CA_CERT_PATH` | string | `certs/SUSE_Trust_Root.crt` | Path to SUSE internal CA certificate for TLS validation of all connections to *.suse.de services (HTTP, LDAP, AMQP). Combined with system CA bundle at runtime. | `docs/features/platform/networking.md` |

Note: operational parameters for the `sync_ldap_directory` fetcher
(`max_deactivations`, `ldap_connect_timeout`, `ldap_operation_timeout`,
`retry_max_attempts`) are configured via custom settings in the admin
dashboard, not via environment variables. See
`docs/features/identity/ad-integration.md` for details and
`docs/features/platform/fetcher-infrastructure.md`, "Custom Settings
Schema" for the configuration mechanism.

## SMELT / AIMAAS

| Env Var | Type | Default | Description | Defined in |
|---------|------|---------|-------------|------------|
| `SMELT_API_URL` | string | `https://smelt.suse.de/api` | SMELT API base URL for product catalog sync and package resolution | `docs/features/packages/product-catalog.md` |
| `AIMAAS_API_URL` | string | `https://aimaas.suse.de/api` | AIMAAS API base URL for product lifecycle and CVSS threshold sync | `docs/features/packages/product-catalog.md` |

Note: authentication requirements for SMELT and AIMAAS are TBD (see
`docs/data-sources.md`). When defined, credential env vars will be added
here.

## External APIs

| Env Var | Type | Default | Description | Defined in |
|---------|------|---------|-------------|------------|
| `NVD_API_KEY` | string | `""` (optional) | NVD API key for higher rate limits on CVE fetching. When configured, consider reducing the `sync_nvd_cves` fetcher's `request_delay` from 6.0s to ~0.6s via the fetcher admin dashboard | `docs/features/tickets/cve-sync-nvd.md` |
| `GITHUB_TOKEN` | string | `""` (required for `sync_ghsa_advisories`) | GitHub personal access token for GHSA advisory sync. Without token: 60 req/hour (insufficient for production). With token: 5,000 req/hour. The fetcher refuses to execute if this is empty or unset | `docs/features/tickets/cve-sync-ghsa.md` |

## Git-Based Fetchers

| Env Var | Type | Default | Description | Defined in |
|---------|------|---------|-------------|------------|
| `GIT_CLONE_BASE_DIR` | string (path) | `/var/lib/sentinel/git` | Base directory for persistent bare clones used by git-based fetchers (`sync_mitre_cves`, `sync_kernel_cves`). Must be backed by persistent storage in containerized deployments | `docs/features/platform/git-fetcher-infrastructure.md` |

## Application

| Env Var | Type | Default | Description | Defined in |
|---------|------|---------|-------------|------------|
| `APP_NAME` | string | `sentinel` | Application name (used in logs, health endpoint) | `docs/architecture.md` |
| `DEBUG` | bool | `false` | Enable debug mode (never in production) | `docs/architecture.md` |
| `CORS_ORIGINS` | list (comma-separated) | `http://localhost:5173` | Allowed CORS origins for the frontend | `docs/architecture.md` |

### Standard Environment Variables (Non-Sentinel)

These are standard system-level variables respected by the HTTP client
library (httpx). They are NOT Sentinel-specific and are typically set at
the container or system level.

| Variable | Type | Default | Description | Defined in |
|----------|------|---------|-------------|------------|
| `HTTPS_PROXY` | string | (none) | Proxy URL for outgoing HTTPS connections. Respected by all HTTP clients | `docs/features/platform/networking.md` |
| `HTTP_PROXY` | string | (none) | Proxy URL for outgoing HTTP connections | `docs/features/platform/networking.md` |
| `NO_PROXY` | string | (none) | Comma-separated hosts that bypass the proxy | `docs/features/platform/networking.md` |
| `LC_ALL` | string | (none) | Locale override. Set to `C` in `git_operations.py` subprocess calls (code-level guarantee). Recommended as `C` at container level for defense-in-depth | `docs/features/platform/git-fetcher-infrastructure.md` |
| `GIT_TERMINAL_PROMPT` | string | (none) | Git prompt control. Set to `0` in `git_operations.py` subprocess calls (code-level guarantee). Prevents interactive prompts that would block async workers | `docs/features/platform/git-fetcher-infrastructure.md` |
| `TZ` | string | (none) | Timezone. Set to `UTC` in `git_operations.py` subprocess calls and recommended at container level (see `docs/deployment.md`, Timezone and Locale Requirements) | `docs/features/platform/git-fetcher-infrastructure.md`, `docs/deployment.md` |

## Runtime Database Settings

These are not environment variables. They are stored in the database and
managed via the Admin API (`PATCH /api/v1/admin/settings`).

| Setting | Type | Default | Description | Defined in |
|---------|------|---------|-------------|------------|
| `default_cvss_version` | string | `"3.1"` | System-wide CVSS version for severity and eligibility. Allowed: `"3.1"`, `"4.0"` | `docs/features/tickets/cvss-scoring.md` |

## Notes for Operators

1. **Secrets must never be committed** to the repository or baked into
   container images. Use `.env` files for local development,
   ConfigMaps/Secrets for Kubernetes.

2. **`JWT_SECRET_KEY` must not be reused** for other purposes (e.g.,
   external integrations, webhook signing). Rotate it only during
   planned maintenance windows — rotation invalidates all active JWTs
   (mass logout).

3. **Startup validation**: the application validates all required settings
   at boot and fails fast with a clear error message indicating which
   variable is missing or invalid.

4. **Naming convention**: environment variables use `UPPER_SNAKE_CASE`.
   The corresponding Python setting uses `lower_snake_case`. The mapping
   is 1:1 (e.g., `JWT_SECRET_KEY` → `jwt_secret_key`).
