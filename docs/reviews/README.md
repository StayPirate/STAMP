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
| [audit-trail-infrastructure](audit-trail-infrastructure.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-05-15 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [authentication](authentication.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-05-07 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [identity-audit-log](identity-audit-log.md) | 7 | 2 | 3 | 4 | 🟢 | 16 | 2026-05-15 | ⚠️ |
|  | 4:🟠 3:🟡 | 1:🔴 1:🟠 | 1:🟠 2:🟡 | 4:🟡 |  |  |  |  |
| [local-authentication](local-authentication.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-05-07 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [rbac](rbac.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-05-15 | |
|  |  |  |  |  |  |  |  |  |
| [sso-authentication](sso-authentication.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-05-07 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [ticket-audit-log](ticket-audit-log.md) | — | — | — | — | — | 0 | — | — |
|  |  |  |  |  |  |  |  |  |
| [user-management](user-management.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-05-09 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [user-service](user-service.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0 | 2026-05-08 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| **Total** | **7** | **2** | **3** | **4** | **0** | **16** |  |  |
|  | 4:🟠 3:🟡 | 1:🔴 1:🟠 | 1:🟠 2:🟡 | 4:🟡 |  |  |  |  |

### Disabled specs

- admin
- api-key-service
- cve-tracking
- cvss-scoring
- fetcher-dashboard
- fetcher-infrastructure
- git-product-release-detection
- git-track-release-detection
- ibs-integration
- ibs-product-release-detection
- ibs-rabbitmq-integration
- ibs-submission-tracking
- ibs-track-release-detection
- maintainer-dashboard
- package-bugowner
- package-tracking
- pages
- product-catalog
- product-lifecycle-transitions
- references
- tickets
