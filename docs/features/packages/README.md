# Packages

Package affectedness, release detection, product catalog, and submission
tracking.

## Specs

```
package-model.md                      Status model, eligibility, delivery, soft-deletion
├── ibs-track-release-detection.md       IBS track-level: MD5 cache, IBS diff, Cases A/B/C
├── ibs-product-release-detection.md     IBS product-level: updateinfo.xml, advisory match chain
├── git-track-release-detection.md       Git track-level release detection (TBD)
├── git-product-release-detection.md     Git product-level release detection (TBD)
└── product-lifecycle-transitions.md     Reactive LTSS / EOL automation

package-service.md                       package_service module contract (mutations, orchestration, queries)
product-catalog.md                       Product/ProductRepository, SMELT/AIMAAS sync, lifecycle phases
ibs-submission-tracking.md               SR/RR tracking via RabbitMQ + periodic sync
package-bugowner.md                      IBS bugowner resolution and cache
maintainer.md                            Maintainer operations (pending fixes, in-progress, completed)
```

## Relationships

- `package-model.md` is the umbrella spec for the affectedness model.
  The two release-detection specs and the lifecycle-transitions spec
  implement specific automation described there.
- `product-catalog.md` owns the Product and ProductRepository entities,
  SMELT product sync, AIMAAS lifecycle/threshold sync, and the
  `GET /api/v1/products` endpoint. `package-model.md` consumes
  product data for eligibility evaluation and track-to-product mapping.
- `package-service.md` is the service-layer companion to
  `package-model.md` — it centralizes all package-centric mutations
  (track status, delivery, product eligibility, soft-delete/restore),
  orchestration (`add_package_to_ticket`), and query operations.
  Depends on `ticket_mutations.reconcile_ticket_status()`.
- `ibs-submission-tracking.md` is independent but shares the
  `TicketPackageTrack` model and `IBSEventConsumer` infrastructure.
- `package-bugowner.md` is self-contained — it caches IBS maintainer
  data referenced by the maintainer operations spec.
