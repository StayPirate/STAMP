# Packages

Package affectedness, release detection, and submission tracking.

## Specs

```
package-tracking.md                  Status model, eligibility, add/remove packages
├── ibs-codestream-release-detection.md  MD5 cache, IBS diff, Cases A/B/C
├── ibs-product-release-detection.md     updateinfo.xml, advisory match chain
└── product-lifecycle-transitions.md     Reactive LTSS / EOL automation

ibs-submission-tracking.md           SR/RR tracking via RabbitMQ + periodic sync
package-bugowner.md                  IBS bugowner resolution and cache
```

## Relationships

- `package-tracking.md` is the umbrella spec for the affectedness model.
  The two release-detection specs and the lifecycle-transitions spec
  implement specific automation described there.
- `ibs-submission-tracking.md` is independent but shares the
  `TicketPackageCodestream` model and `IBSEventConsumer` infrastructure.
- `package-bugowner.md` is self-contained — it caches IBS maintainer
  data referenced by the ticket detail UI.
