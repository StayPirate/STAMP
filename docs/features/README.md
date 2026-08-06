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

## All Specs

### Identity

- [authentication.md](identity/authentication.md) — Session/JWT framework and credential validation
- [sso-authentication.md](identity/sso-authentication.md) — OIDC SSO login flow (deferred)
- [local-authentication.md](identity/local-authentication.md) — Username/password login, lockout
- [identity-provisioning.md](identity/identity-provisioning.md) — External identity provisioning, role mapping (deferred)
- [user-service.md](identity/user-service.md) — Service-layer contract for user mutations
- [user-management.md](identity/user-management.md) — Admin CLI and API for user operations
- [rbac.md](identity/rbac.md) — Role definitions and endpoint permission map
- [identity-audit-log.md](identity/identity-audit-log.md) — Identity audit trail (IdentityAuditEvent)
- [api-key-management.md](identity/api-key-management.md) — API key lifecycle, REST API, and CLI
- [api-key-service.md](identity/api-key-service.md) — API key mutation and query service

### Integrations

- [ibs-integration.md](integrations/ibs-integration.md) — IBS REST API client, endpoints, authentication
- [ibs-rabbitmq-integration.md](integrations/ibs-rabbitmq-integration.md) — IBS RabbitMQ consumer, connection management

### Packages

- [package-service.md](packages/package-service.md) — Service-layer contract for package mutations and queries
- [package-model.md](packages/package-model.md) — Status model, eligibility, add/remove packages
- [ibs-track-release-detection.md](packages/ibs-track-release-detection.md) — MD5 cache, IBS diff
- [ibs-product-release-detection.md](packages/ibs-product-release-detection.md) — updateinfo.xml, advisory match
- [git-track-release-detection.md](packages/git-track-release-detection.md) — Git track-level release detection
- [git-product-release-detection.md](packages/git-product-release-detection.md) — Git product-level release detection
- [product-lifecycle-transitions.md](packages/product-lifecycle-transitions.md) — Reactive LTSS / EOL automation
- [product-catalog.md](packages/product-catalog.md) — Product/ProductRepository, SMELT/AIMAAS sync
- [ibs-submission-tracking.md](packages/ibs-submission-tracking.md) — SR/RR tracking via RabbitMQ + periodic sync
- [package-bugowner.md](packages/package-bugowner.md) — IBS bugowner resolution and cache
- [maintainer.md](packages/maintainer.md) — Maintainer operations (pending fixes, in-progress, completed)
- [cpe-package-mapping.md](packages/cpe-package-mapping.md) — CPE-to-package resolution via static mapping file

### Platform

- [fetcher-infrastructure.md](platform/fetcher-infrastructure.md) — BaseFetcher base class, registry, execution tracking
- [fetcher-operations.md](platform/fetcher-operations.md) — Monitoring, API, and CLI diagnostics for fetchers
- [audit-trail-infrastructure.md](platform/audit-trail-infrastructure.md) — BaseAuditLog base class, AuditEventMixin
- [system-settings.md](platform/system-settings.md) — System settings (default CVSS version, etc.)
- [testing-strategy.md](platform/testing-strategy.md) — Testing methodology, infrastructure, fixtures, and coverage policy

### Tickets

- [tickets.md](tickets/tickets.md) — Ticket lifecycle, status gates, centralized evaluation
- [ticket-service.md](tickets/ticket-service.md) — Service-layer contract for non-gate ticket lifecycle operations and confidentiality management
- [ticket-mutations.md](tickets/ticket-mutations.md) — Gate-relevant mutations, status evaluation, manual-zone exits
- [cve-tracking.md](tickets/cve-tracking.md) — CVE tracking feature (business rules, API endpoints, CVE rejection handling)
- [cve-sync-nvd.md](tickets/cve-sync-nvd.md) — NVD fetcher specification
- [cve-sync-mitre.md](tickets/cve-sync-mitre.md) — MITRE cvelistV5 fetcher specification
- [cve-sync-kernel.md](tickets/cve-sync-kernel.md) — Linux Kernel CNA fetcher specification
- [cve-sync-ghsa.md](tickets/cve-sync-ghsa.md) — GitHub Advisory DB fetcher specification
- [cve-sync-osv.md](tickets/cve-sync-osv.md) — OSV enrichment fetcher specification
- [cve-sync-redhat.md](tickets/cve-sync-redhat.md) — Red Hat Security Data fetcher specification
- [cve-sync-kev.md](tickets/cve-sync-kev.md) — CISA KEV fetcher (planned)
- [cve-sync-epss.md](tickets/cve-sync-epss.md) — EPSS fetcher (planned)
- [cvss-scoring.md](tickets/cvss-scoring.md) — Multi-provider CVSS assessments, severity resolution
- [ticket-audit-log.md](tickets/ticket-audit-log.md) — TicketAuditEvent audit trail, event type contract
- [ticket-references.md](tickets/ticket-references.md) — External links on tickets (auto-classified by type, manual with manage_references capability)
