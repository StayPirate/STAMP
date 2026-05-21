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
| [ad-integration](ad-integration.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-05-12 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [api-key-service](api-key-service.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-05-17 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [audit-trail-infrastructure](audit-trail-infrastructure.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-05-17 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [authentication](authentication.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-05-18 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [identity-audit-log](identity-audit-log.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-05-16 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [local-authentication](local-authentication.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-05-07 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [package-model](package-model.md) | 2 | 1 | 🟢 | 2 | 3 | 8 | 2026-05-21 |  |
| | 2:🟡 | 1:🟡 |  | 2:🟡 | 3:🟡 |  |  |  |
| [package-service](package-service.md) | 13 | — | — | — | — | 13 | 2026-05-21 |  |
| | 3:🔴 7:🟠 3:🟡 |  |  |  |  |  |  |  |
| [rbac](rbac.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-05-15 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [sso-authentication](sso-authentication.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-05-07 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [ticket-audit-log](ticket-audit-log.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-05-17 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [ticket-mutations](ticket-mutations.md) | — | — | — | — | — | 0 |  | — |
|  |  |  |  |  |  |  |  |  |
| [tickets](tickets.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-05-20 |  |
|  |  |  |  |  |  |  |  |  |
| [user-management](user-management.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-05-09 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [user-service](user-service.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-05-08 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| **Total** | **15** | **1** | **0** | **2** | **3** | **21** |  |  |
| | 3:🔴 7:🟠 5:🟡 | 1:🟡 |  | 2:🟡 | 3:🟡 |  |  |  |

### Disabled specs

- admin
- cve-tracking
- cvss-scoring
- fetcher-infrastructure
- fetcher-operations
- git-product-release-detection
- git-track-release-detection
- ibs-integration
- ibs-product-release-detection
- ibs-rabbitmq-integration
- ibs-submission-tracking
- ibs-track-release-detection
- maintainer
- package-bugowner
- product-catalog
- product-lifecycle-transitions
- ticket-references
