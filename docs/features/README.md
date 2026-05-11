# Feature Specifications

Index of all feature specification domains.

## Domains

| Domain | Description |
|--------|-------------|
| [Identity](identity/) | User authentication, authorization, and lifecycle management |
| [Integrations](integrations/) | Technical integration layers with external services |
| [Packages](packages/) | Package affectedness, release detection, and submission tracking |
| [Platform](platform/) | Cross-cutting infrastructure and system administration |
| [Tickets](tickets/) | CVE ingestion, triage, severity, and audit trail |
| [UI](ui/) | Page specifications, dashboards, and cross-cutting UI features |

## All Specs

### Identity

- [authentication.md](identity/authentication.md) — Session/JWT/API-key framework
- [sso-authentication.md](identity/sso-authentication.md) — OIDC SSO login flow
- [local-authentication.md](identity/local-authentication.md) — Username/password login, lockout
- [ad-integration.md](identity/ad-integration.md) — SUSE AD sync, role mapping
- [user-service.md](identity/user-service.md) — Service-layer contract for user mutations
- [user-management.md](identity/user-management.md) — Admin CLI and API for user operations
- [rbac.md](identity/rbac.md) — Role definitions and endpoint permission map

### Integrations

- [ibs-integration.md](integrations/ibs-integration.md) — IBS REST API client, endpoints, authentication
- [ibs-rabbitmq-integration.md](integrations/ibs-rabbitmq-integration.md) — IBS RabbitMQ consumer, connection management

### Packages

- [package-tracking.md](packages/package-tracking.md) — Status model, eligibility, add/remove packages
- [ibs-codestream-release-detection.md](packages/ibs-codestream-release-detection.md) — MD5 cache, IBS diff
- [ibs-product-release-detection.md](packages/ibs-product-release-detection.md) — updateinfo.xml, advisory match
- [product-lifecycle-transitions.md](packages/product-lifecycle-transitions.md) — Reactive LTSS / EOL automation
- [ibs-submission-tracking.md](packages/ibs-submission-tracking.md) — SR/RR tracking via RabbitMQ + periodic sync
- [package-bugowner.md](packages/package-bugowner.md) — IBS bugowner resolution and cache

### Platform

- [fetcher-infrastructure.md](platform/fetcher-infrastructure.md) — BaseFetcher base class, registry, execution tracking
- [fetcher-dashboard.md](platform/fetcher-dashboard.md) — Monitoring dashboard, API, and CLI for fetchers
- [admin.md](platform/admin.md) — System settings (default CVSS version, etc.)

### Tickets

- [tickets.md](tickets/tickets.md) — Ticket lifecycle, status gates, centralized evaluation
- [cve-tracking.md](tickets/cve-tracking.md) — CVE ingestion from NVD/MITRE, on-demand fetch
- [cvss-scoring.md](tickets/cvss-scoring.md) — Multi-provider CVSS assessments, severity resolution
- [ticket-history.md](tickets/ticket-history.md) — TicketEvent audit trail, event type contract

### UI

- [pages.md](ui/pages.md) — Page index and routing overview
- [maintainer-dashboard.md](ui/maintainer-dashboard.md) — Package maintainer view (My Packages)
- [references.md](ui/references.md) — External links on tickets (auto + manual)
