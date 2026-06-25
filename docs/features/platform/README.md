# Platform

Cross-cutting infrastructure and system administration.

## Specs

```
fetcher-infrastructure.md       BaseFetcher base class, registry, execution tracking
cve-fetcher-infrastructure.md   BaseCVEFetcher base class, CVE fetcher conventions
git-fetcher-infrastructure.md   BaseGitFetcher base class, git_operations module
networking.md                   HTTP client (httpx), TLS configuration, SUSE CA
fetcher-operations.md           Monitoring, API, and CLI diagnostics for fetchers
audit-trail-infrastructure.md   BaseAuditLog base class, AuditEventMixin
system-settings.md              System settings (default CVSS version, etc.)
```

## Relationships

- `fetcher-infrastructure.md` defines the base class contract that all
  background data-fetching tasks inherit from.
- `cve-fetcher-infrastructure.md` extends `fetcher-infrastructure.md` with
  the `BaseCVEFetcher` contract for CVE-specific fetchers.
- `git-fetcher-infrastructure.md` extends `cve-fetcher-infrastructure.md`
  with the `BaseGitFetcher` contract for git-based delta-flow fetchers.
- `networking.md` defines the shared HTTP client (`httpx`) and TLS
  configuration used by all fetchers and services.
- `fetcher-operations.md` is the monitoring layer built on top of
  `fetcher-infrastructure.md` — it consumes `FetcherRun` records.
- `system-settings.md` defines the system settings API; settings like
  `default_cvss_version` are consumed by `tickets/cvss-scoring.md`.
