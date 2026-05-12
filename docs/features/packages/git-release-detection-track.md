# Git Track-Level Release Detection

Release detection at the track level for the git workflow — the equivalent
of `ibs-codestream-release-detection.md` for IBS tracks.

**Status**: TBD — mechanism not yet defined.

## Context

This specification will define how Sentinel detects that a security fix
has been applied to a git branch (e.g., `slfo-main`, `slfo-1.2`) on
`src.suse.de`. When a fix is detected, the corresponding
`TicketPackageTrack` (with `workflow_type = 'git'`) will have its
`status` set to `FIXED` and `delivery_status` set to `RELEASED`.

See `docs/features/packages/package-tracking.md` for the package tracking
model, including the three orthogonal dimensions (affectedness,
eligibility, delivery) and the workflow-agnostic design that this
specification extends.

## Open Questions

- What mechanism detects changes in git branches? (webhook from
  src.suse.de? polling? event bus?)
- How is the CVE fix identified in a git commit? (commit message
  convention? changelog parsing? diff analysis similar to IBS?)
- Is there an equivalent of the MD5 cache
  (`CodestreamPackageChecksum`) for git, or is the detection mechanism
  fundamentally different?
- What is the periodic catch-up strategy? (equivalent of the 24h
  `check_codestream_releases` fetcher for IBS)

## Cross-references

- `docs/features/packages/package-tracking.md` — package tracking model
  (owning specification)
- `docs/features/packages/ibs-codestream-release-detection.md` — IBS
  equivalent of this specification
- `docs/data-model.md` — TicketPackageTrack entity with `workflow_type`
  discriminator
