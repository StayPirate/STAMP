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
The IBS repository, redirect, advisory, and source-package contracts are
source-specific and are not presumed to apply to Git/SLFO Products. This
specification requires its own upstream evidence before selecting a mechanism.

See `docs/features/packages/package-model.md` for the package tracking
model, including the three orthogonal dimensions (affectedness,
eligibility, delivery) and the workflow-agnostic design that this
specification extends.

## Open Questions

- Do SLFO products publish release metadata that can authoritatively relate an
  exact source package and CVE to a Product publication?
- If not, what mechanism confirms that the fix has reached the product's
  repository?
- If an advisory format exists, which fields, integrity metadata, resource
  bounds, and release-time semantics are authoritative?

## Cross-references

- `docs/features/packages/package-model.md` — package tracking model
  (owning specification)
- `docs/features/packages/ibs-product-release-detection.md` — IBS
  equivalent of this specification
- `docs/data-model.md` — TicketPackageProduct entity with `released_at`
  field
