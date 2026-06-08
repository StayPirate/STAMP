# Git Track-Level Release Detection

Release detection at the track level for the git workflow — the equivalent
of `ibs-track-release-detection.md` for IBS tracks.

**Status**: TBD — mechanism not yet defined.

## Context

This specification will define how Sentinel detects that a security fix
has been applied to a git branch (e.g., `slfo-main`, `slfo-1.2`) on
`src.suse.de`. When a fix is detected, the corresponding
`TicketPackageTrack` (with `workflow_type = 'git'`) is updated via two
distinct service calls within a single database transaction:

1. `package_service.set_track_status(track, status=FIXED)` — updates
   the affectedness dimension
2. `package_service.set_track_delivery_status(track,
   delivery_status=RELEASED)` — updates the delivery dimension

For git-based tracks, the fix landing in the repository IS the delivery
event (there is no separate SR/RR workflow), but the two dimensions are
still updated through separate service calls to maintain: (a) separate
audit trail events for each dimension, (b) consistency with the IBS
model, and (c) the ability to independently test each transition. Both
calls execute within a single database transaction owned by the caller
(git release detector). If either fails, the entire transaction rolls
back — the track never reaches an inconsistent state where only one
dimension is updated.

See `docs/features/packages/package-model.md` for the package tracking
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
  `detect_ibs_track_releases` fetcher for IBS)

## Cross-references

- `docs/features/packages/package-model.md` — package tracking model
  (owning specification)
- `docs/features/packages/ibs-track-release-detection.md` — IBS
  equivalent of this specification
- `docs/data-model.md` — TicketPackageTrack entity with `workflow_type`
  discriminator
