# Spec Review Index

Summary of specification reviews conducted by the 5-reviewer pipeline
(Gap Analysis, Coherence, Design, Security, API Conventions).

## Legend

| Symbol | Meaning |
|--------|---------|
| `—` | Reviewer never executed on this spec |
| `🟢` | Reviewer executed, zero open findings |
| `N` | Reviewer executed, N open findings |
| `⚠️` | Spec modified after last review (stale) |

Severity indicators (sub-row): `🔴` = High, `🟠` = Medium, `🟡` = Low

## Summary Table

| Spec | GAP | COH | DES | SEC | API | Total | Last Review | Stale |
|------|-----|-----|-----|-----|-----|-------|-------------|-------|
| [ad-integration](ad-integration.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-05-15 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [api-key-service](api-key-service.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-05-17 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [audit-trail-infrastructure](audit-trail-infrastructure.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-05-15 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [authentication](authentication.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-05-18 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [identity-audit-log](identity-audit-log.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-05-16 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [local-authentication](local-authentication.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-05-14 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [rbac](rbac.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-05-15 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [sso-authentication](sso-authentication.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-05-14 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [ticket-audit-log](ticket-audit-log.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-05-17 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [tickets](tickets.md) | 12 | 1 | 6 | 9 | 5 | 33 | 2026-05-19 |  |
| | 5:🟠 7:🟡 | 1:🟡 | 3:🟠 3:🟡 | 4:🟠 5:🟡 | 1:🟠 4:🟡 |  |  |  |
| [user-management](user-management.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-05-14 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [user-service](user-service.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-05-15 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| **Total** | **12** | **1** | **6** | **9** | **5** | **33** |  |  |
| | 5:🟠 7:🟡 | 1:🟡 | 3:🟠 3:🟡 | 4:🟠 5:🟡 | 1:🟠 4:🟡 |  |  |  |

### Disabled specs

- admin
- admin-settings
- all-tickets
- cve-tracking
- cvss-scoring
- fetcher-dashboard
- fetcher-detail
- fetcher-infrastructure
- fetchers
- git-product-release-detection
- git-track-release-detection
- ibs-integration
- ibs-product-release-detection
- ibs-rabbitmq-integration
- ibs-submission-tracking
- ibs-track-release-detection
- inbox
- layout
- login
- maintainer-dashboard
- my-packages
- my-packages-ticket
- my-tickets
- orphan-tickets
- package-bugowner
- package-tracking
- pages
- product-catalog
- product-lifecycle-transitions
- references
- ticket-detail
