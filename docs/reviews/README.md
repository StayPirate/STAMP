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
| [cpe-package-mapping](cpe-package-mapping.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-06-01 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [cve-service](cve-service.md) | 🟢 | 1 | 🟢 | 🟢 | 🟢 | 1 | 2026-06-05 | ⚠️ |
| |  | 1:🟡 |  |  |  |  |  |  |
| [cvss-scoring](cvss-scoring.md) | 8 | 9 | — | — | — | 17 | 2026-06-07 |  |
| | 1:🔴 5:🟠 2:🟡 | 1:🔴 2:🟠 6:🟡 |  |  |  |  |  |  |
| [fetcher-infrastructure](fetcher-infrastructure.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-05-28 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [fetcher-operations](fetcher-operations.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-05-28 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [identity-audit-log](identity-audit-log.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-05-16 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [local-authentication](local-authentication.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-05-07 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [package-model](package-model.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-05-22 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [package-service](package-service.md) | 3 | 1 | 🟢 | 🟢 | 🟢 | 4 | 2026-06-03 | ⚠️ |
| | 2:🟠 1:🟡 | 1:🟡 |  |  |  |  |  |  |
| [rbac](rbac.md) | 🟢 | 🟢 | 🟢 | 1 | 🟢 | 1 | 2026-05-26 | ⚠️ |
| |  |  |  | 1:🟡 |  |  |  |  |
| [sso-authentication](sso-authentication.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-05-07 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [ticket-audit-log](ticket-audit-log.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-05-17 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [ticket-mutations](ticket-mutations.md) | 🟢 | 1 | 1 | 🟢 | 🟢 | 2 | 2026-06-03 | ⚠️ |
| |  | 1:🟡 | 1:🟡 |  |  |  |  |  |
| [ticket-references](ticket-references.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-05-30 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [ticket-service](ticket-service.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-05-26 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [tickets](tickets.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-05-20 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [user-management](user-management.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-05-09 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [user-service](user-service.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-05-08 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| **Total** | **11** | **12** | **1** | **1** | **0** | **25** |  |  |
| | 1:🔴 7:🟠 3:🟡 | 1:🔴 2:🟠 9:🟡 | 1:🟡 | 1:🟡 |  |  |  |  |

### Disabled specs

- cve-tracking
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
- system-settings
