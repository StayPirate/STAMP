# UI

Page specifications, dashboards, and cross-cutting UI features.

## Specs

```
pages.md                  Page index and routing overview
pages/                    Individual page specifications (12 pages)
maintainer-dashboard.md   Package maintainer view (My Packages)
references.md             External links on tickets (auto + manual)
```

## Relationships

- `pages.md` is the index — it lists all pages and links to their
  individual specs in `pages/`.
- `maintainer-dashboard.md` defines a role-specific view that aggregates
  data from `packages/package-tracking.md` and
  `packages/ibs-submission-tracking.md`.
- `references.md` defines the ticket references feature (links created
  by CVE fetchers and manually by VAs), displayed in the ticket detail
  page (`pages/ticket-detail.md`).
