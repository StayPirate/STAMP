# Packages

Package affectedness, release detection, and submission tracking.

## Specs

```
package-tracking.md                      Status model, eligibility, delivery, soft-deletion
├── ibs-codestream-release-detection.md  IBS track-level: MD5 cache, IBS diff, Cases A/B/C
├── ibs-product-release-detection.md     IBS product-level: updateinfo.xml, advisory match chain
├── git-release-detection-track.md       Git track-level release detection (TBD)
├── git-release-detection-product.md     Git product-level release detection (TBD)
└── product-lifecycle-transitions.md     Reactive LTSS / EOL automation

ibs-submission-tracking.md               SR/RR tracking via RabbitMQ + periodic sync
package-bugowner.md                      IBS bugowner resolution and cache
```

## Relationships

- `package-tracking.md` is the umbrella spec for the affectedness model.
  The two release-detection specs and the lifecycle-transitions spec
  implement specific automation described there.
- `ibs-submission-tracking.md` is independent but shares the
  `TicketPackageTrack` model and `IBSEventConsumer` infrastructure.
- `package-bugowner.md` is self-contained — it caches IBS maintainer
  data referenced by the ticket detail UI.
