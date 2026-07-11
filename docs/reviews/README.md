# Spec Review Index

Summary of specification reviews conducted by the 5-reviewer pipeline
(Gap Analysis, Coherence, Design, Security, API Conventions).

## Summary Table

| Spec | GAP | COH | DES | SEC | API | Open | Last Review | Stale |
|------|-----|-----|-----|-----|-----|------|-------------|-------|
| [ad-integration](ad-integration.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0/91 | 2026-05-12 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [api-key-service](api-key-service.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0/24 | 2026-05-17 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [audit-trail-infrastructure](audit-trail-infrastructure.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0/18 | 2026-05-17 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [authentication](authentication.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0/64 | 2026-05-18 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [cpe-package-mapping](cpe-package-mapping.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0/13 | 2026-06-01 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [cve-fetcher-infrastructure](cve-fetcher-infrastructure.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0/11 | 2026-06-29 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [cve-record-parser](cve-record-parser.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0/5 | 2026-07-09 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [cve-service](cve-service.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0/39 | 2026-06-05 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [cve-sync-epss](cve-sync-epss.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0/4 | 2026-07-11 |  |
|  |  |  |  |  |  |  |  |  |
| cve-sync-ghsa | — | — | — | — | — | —/— | — | — |
|  |  |  |  |  |  |  |  |  |
| [cve-sync-kernel](cve-sync-kernel.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0/9 | 2026-07-09 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [cve-sync-kev](cve-sync-kev.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0/0 | 2026-07-07 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [cve-sync-mitre](cve-sync-mitre.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0/14 | 2026-07-09 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| cve-sync-nvd | — | — | — | — | — | —/— | — | — |
|  |  |  |  |  |  |  |  |  |
| [cve-sync-osv](cve-sync-osv.md) | 4 | 1 | 1 | 🟢 | 🟢 | 6/6 | 2026-07-11 |  |
| | 4:🟠 | 1:🟡 | 1:🔴 |  |  |  |  |  |
| [cve-sync-redhat](cve-sync-redhat.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0/3 | 2026-07-11 |  |
|  |  |  |  |  |  |  |  |  |
| [cve-tracking](cve-tracking.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0/22 | 2026-06-24 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [cvss-scoring](cvss-scoring.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0/49 | 2026-06-10 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [fetcher-infrastructure](fetcher-infrastructure.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0/31 | 2026-06-29 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [fetcher-operations](fetcher-operations.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0/24 | 2026-05-28 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [git-fetcher-infrastructure](git-fetcher-infrastructure.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0/15 | 2026-07-02 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [health-endpoints](health-endpoints.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0/4 | 2026-07-03 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [identity-audit-log](identity-audit-log.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0/16 | 2026-05-16 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [local-authentication](local-authentication.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0/21 | 2026-05-07 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [networking](networking.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0/13 | 2026-07-02 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [package-model](package-model.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0/25 | 2026-05-22 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [package-service](package-service.md) | 2 | 🟢 | 🟢 | 🟢 | 🟢 | 2/30 | 2026-06-03 | ⚠️ |
| | 2:🟠 |  |  |  |  |  |  |  |
| [rbac](rbac.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0/43 | 2026-05-26 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [sso-authentication](sso-authentication.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0/25 | 2026-05-07 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [system-settings](system-settings.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0/8 | 2026-07-03 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [ticket-audit-log](ticket-audit-log.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0/15 | 2026-05-17 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [ticket-mutations](ticket-mutations.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0/41 | 2026-06-03 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [ticket-references](ticket-references.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0/32 | 2026-05-30 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [ticket-service](ticket-service.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0/28 | 2026-05-26 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [tickets](tickets.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0/23 | 2026-05-20 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [user-management](user-management.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0/93 | 2026-05-09 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| [user-service](user-service.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0/23 | 2026-05-08 | ⚠️ |
|  |  |  |  |  |  |  |  |  |
| **Total** | **6** | **1** | **1** | **🟢** | **🟢** | **8/889** |  |  |
| | 6:🟠 | 1:🟡 | 1:🔴 |  |  |  |  |  |

### Disabled specs

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

## Legend

| Symbol | Meaning |
|--------|---------|
| `—` | Reviewer never executed on this spec |
| `🟢` | Reviewer executed, zero open findings |
| `N` | Reviewer executed, N open findings |
| `⚠️` | Spec modified after last review (stale) |

Severity indicators (sub-row): `🔴` = High, `🟠` = Medium, `🟡` = Low
