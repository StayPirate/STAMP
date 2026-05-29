# Platform

Cross-cutting infrastructure and system administration.

## Specs

```
fetcher-infrastructure.md       BaseFetcher base class, registry, execution tracking
fetcher-operations.md           Monitoring, API, and CLI diagnostics for fetchers
audit-trail-infrastructure.md   BaseAuditLog base class, AuditEventMixin
system-settings.md              System settings (default CVSS version, etc.)
```

## Relationships

- `fetcher-infrastructure.md` defines the base class contract that all
  background data-fetching tasks inherit from.
- `fetcher-operations.md` is the monitoring layer built on top of
  `fetcher-infrastructure.md` — it consumes `FetcherRun` records.
- `system-settings.md` defines the system settings API; settings like
  `default_cvss_version` are consumed by `tickets/cvss-scoring.md`.
