# Platform

Cross-cutting infrastructure and system administration.

## Specs

```
fetcher-infrastructure.md   BaseFetcher base class, registry, execution tracking
fetcher-dashboard.md        Monitoring dashboard, API, and CLI for fetchers
admin.md                    System settings (default CVSS version, etc.)
```

## Relationships

- `fetcher-infrastructure.md` defines the base class contract that all
  background data-fetching tasks inherit from.
- `fetcher-dashboard.md` is the monitoring layer built on top of
  `fetcher-infrastructure.md` — it consumes `FetcherRun` records.
- `admin.md` defines the system settings API; settings like
  `default_cvss_version` are consumed by `tickets/cvss-scoring.md`.
