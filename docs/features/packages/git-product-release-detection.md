# Git Product-Level Release Detection

Release detection at the product level for the git workflow — the
equivalent of `ibs-product-release-detection.md` for IBS products.

**Status**: TBD — mechanism not yet defined.

## Context

This specification will define how Sentinel confirms that a security fix
has been published to the update repository of a product served by a git
track. When confirmed, the corresponding `TicketPackageProduct` will have
its `released_at` timestamp set.

For IBS products, this confirmation is done via `updateinfo.xml` parsing.
The git workflow may use the same mechanism (if SLFO products publish
`updateinfo.xml`) or a different one.

See `docs/features/packages/package-model.md` for the package tracking
model, including the three orthogonal dimensions (affectedness,
eligibility, delivery) and the workflow-agnostic design that this
specification extends.

## Open Questions

- Do SLFO products publish `updateinfo.xml` in their update
  repositories? If so, the existing `ProductReleaseDetector` may work
  unchanged.
- If not, what mechanism confirms that the fix has reached the product's
  repository?
- Is the advisory format the same as IBS, or does it require a different
  match chain?

## Cross-references

- `docs/features/packages/package-model.md` — package tracking model
  (owning specification)
- `docs/features/packages/ibs-product-release-detection.md` — IBS
  equivalent of this specification
- `docs/data-model.md` — TicketPackageProduct entity with `released_at`
  field
