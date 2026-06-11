# Draft: Standardize "Inactive Status" Terminology

**Origin**: Terminology inconsistency identified during review of
`internalize-post-regression-catchup.md`

**Status**: Ready for implementation

## Problem Statement

The set of statuses `{Resolved, Ignored, Duplicated}` — where tickets are
no longer monitored by background tasks — is referred to inconsistently
across the specification corpus:

| Term used | File | Line |
|-----------|------|------|
| "Inactive tickets" | `tickets.md` | 696 |
| "Terminal Statuses" | `tickets.md` | 699 (section heading) |
| "inactive statuses" | `tickets.md` | 731 |
| "a non-final ticket" | `tickets.md` | 452 |
| "inactive status" | `ticket-service.md` | 521 |
| "inactive status" | `cvss-scoring.md` | 265, 741 |
| "inactive status" | `fetcher-infrastructure.md` | 526 |
| "inactive statuses" | `user-service.md` | 198 |
| "inactive status" | `cve-service.md` | 307 |
| "Inactive tickets" | `data-model.md` | 1087 |
| "non-active tickets" | `data-model.md` | 720 |
| "inactive tickets" | `package-model.md` | 727 |
| "non-final (Analysis or Analyzed)" | `ticket-mutations.md` | 248 |
| "final (Resolved, Ignored, Duplicated)" | `ticket-mutations.md` | 261 |
| "final status (Resolved, Ignored, Duplicated)" | `ticket-references.md` | 387, 408, 409, 413 |
| "non-final status" (referring to tickets) | `open-points.md` | 199 |

The same file (`tickets.md`) uses both "Inactive tickets" (line 696) and
"Terminal Statuses" (line 699) within 3 lines of each other for the same
concept.

"Terminal" is semantically inaccurate: all three statuses admit reverse
transitions (Resolved regresses via gate evaluation; Ignored/Duplicated
are reopened via manual-zone exit operations). These are not terminal in
the formal state-machine sense.

"Final status" is ambiguous: the same term is the canonical name for
`TicketPackageTrack` statuses `{NOT_AFFECTED, FIXED, WONT_FIX}` (defined
in `package-model.md:312-319`). Using it for ticket statuses creates
confusion between two distinct domain concepts.

Additionally, the complementary set `{New, Analysis, Analyzed}` is
referred to as "open tickets" in 11 occurrences across the spec corpus.
While unambiguous, "open" conflicts with the standard terminology because:

- The ticket status set does not include a status called "Open"
- "Open" is used in other domains (IBS request states: "open", "accepted",
  "declined") where it has its own meaning
- "Reopen" already exists as a transition action verb — using "open" as a
  status category creates confusion between state and action

Similarly, "closed" (5 occurrences) and its variants ("closure",
"auto-closed", "manually-closed") are used informally as synonyms for
"inactive status", creating yet another alternative for the same concept.

## Proposed Solution

Standardize on **"inactive status"** (lowercase, no quotes in prose)
as the canonical term for `{Resolved, Ignored, Duplicated}`:

- Already the most commonly used term (8 out of 13 spec occurrences)
- Used by the most authoritative specs for the concept
  (`tickets.md`, `ticket-service.md`, `fetcher-infrastructure.md`,
  `user-service.md`, `cvss-scoring.md`)
- Semantically accurate: tickets are parked/dormant but not irrevocably
  closed
- Already defined at `tickets.md:696`: "Inactive tickets: status
  Resolved, Ignored, or Duplicated. No longer monitored."

The complementary term is **"active status"** for
`{New, Analysis, Analyzed}`.

### What changes

#### Group A: "terminal" / "non-final" / "final" → "inactive" / "active"

| File | Current | Replacement |
|------|---------|-------------|
| `tickets.md:699` | `## Terminal Statuses and Mutability` | `## Inactive Statuses and Mutability` |
| `tickets.md:452` | "a non-final ticket" | "an active ticket" |
| `ticket-mutations.md:248` | "the resulting status is non-final (Analysis or Analyzed)" | "the resulting status is active (Analysis or Analyzed)" |
| `ticket-mutations.md:261` | "If the resulting status is final (Resolved, Ignored, Duplicated)" | "If the resulting status is inactive (Resolved, Ignored, Duplicated)" |
| `data-model.md:720` | "non-active tickets" | "inactive tickets" |
| `ticket-references.md:387` | "in a final status (Resolved, Ignored)" | "in an inactive status (Resolved, Ignored, Duplicated)" |
| `ticket-references.md:408` | "Tickets in final status" | "Tickets in inactive statuses" |
| `ticket-references.md:409` | "tickets in final status (Resolved, Ignored, Duplicated)" | "tickets in inactive statuses (Resolved, Ignored, Duplicated)" |
| `ticket-references.md:413` | "tickets in final status" | "tickets in inactive statuses" |
| `open-points.md:199` | "tickets in non-final status" | "tickets in active statuses" |

#### Group B: "open tickets" → "active tickets"

| File | Line | Current | Replacement |
|------|------|---------|-------------|
| `package-model.md` | 520 | "in open tickets with" | "in active tickets with" |
| `user-service.md` | 594 | "Unassign open tickets: call" | "Unassign active tickets: call" |
| `user-management.md` | 276 | "Count of open tickets assigned to the user that will be unassigned" | "Count of active tickets assigned to the user that will be unassigned" |
| `user-management.md` | 283 | "{n} open tickets will be unassigned" | "{n} active tickets will be unassigned" |
| `user-management.md` | 870 | "Open tickets assigned to this user that will be unassigned" | "Active tickets assigned to this user that will be unassigned" |
| `product-catalog.md` | 146 | "open tickets referencing that product" | "active tickets referencing that product" |
| `product-lifecycle-transitions.md` | 109 | "referencing this product in open\ntickets" | "referencing this product in active\ntickets" |
| `product-lifecycle-transitions.md` | 120 | "referencing this product in open\ntickets" | "referencing this product in active\ntickets" |
| `product-lifecycle-transitions.md` | 133 | "referencing this product in open\ntickets" | "referencing this product in active\ntickets" |
| `ibs-submission-tracking.md` | 674 | "the parent ticket is in an open state" | "the parent ticket is in an active status" |
| `reviews/system-settings.md` | 45 | "open tickets will be re-evaluated" | "active tickets will be re-evaluated" |

#### Group C: "closed" / "closure" / "auto-closed" / "manually-closed" → "inactive" / standard phrasing

| File | Line | Current | Replacement |
|------|------|---------|-------------|
| `ticket-mutations.md` | 262 | "the ticket is closed and does not need an active assignee" | "an inactive ticket does not need an active assignee" |
| `ticket-mutations.md` | 357 | `"""Reject mutations on manually-closed tickets.` | `"""Reject mutations on manual-zone inactive tickets.` |
| `cve-tracking.md` | 334 | "Automatic closure is only safe for tickets that no one has started working" | "Automatic transition to Ignored is only safe for tickets that no one has started working" |
| `cve-tracking.md` | 339 | "auto-closed by the original rejection" | "automatically set to Ignored by the original rejection" |
| `user-service.md` | 199 | "they are closed and do not need an active assignee" | "they no longer need an active assignee" |

#### Group D: Tautology fix in `data-model.md`

| File | Line | Current | Replacement |
|------|------|---------|-------------|
| `data-model.md` | 718 | "For resolved or inactive tickets, the score" | "For inactive tickets, the score" |

Note: "resolved" is a member of the inactive set — including it
separately is redundant. After this fix, lines 717-722 read coherently:
"the frontend SHOULD display the EPSS score only for active tickets. For
inactive tickets, the score reflects the last assessment before the
ticket left the active scope and may be stale. If the UI chooses to
display it for inactive tickets, it SHOULD include a staleness
indicator..."

### What stays unchanged

- `tickets.md:696` — already uses "Inactive tickets"
- `tickets.md:731` — already uses "inactive statuses"
- `ticket-service.md:521` — already uses "inactive status"
- `cvss-scoring.md:265, 741` — already uses "inactive status"
- `fetcher-infrastructure.md:526` — already uses "inactive status"
- `user-service.md:198` — already uses "inactive statuses"
- `cve-service.md:307` — already uses "inactive status"
- `data-model.md:1087` — already uses "Inactive tickets"
- `package-model.md:727` — already uses "inactive tickets"

### False positives excluded

These use "final status", "terminal state", "non-final", "open", or
"closed" in unrelated domains and MUST NOT be changed:

#### "Final status" / "final state" — TicketPackageTrack domain

| File | Line | Term | Context |
|------|------|------|---------|
| `package-model.md` | 312, 316 | "final status" / "Final statuses" | Canonical definition of final/non-final for TicketPackageTrack |
| `package-model.md` | 319 | "final status" / "non-final status" | Cross-reference instruction for the canonical definition |
| `package-model.md` | 607 | "in a final status" | Automatic transitions section |
| `package-service.md` | 158 | "track is in final status" | `set_track_status()` reject message |
| `package-service.md` | 178-179 | "final states" / "final status" | Final-status protection description |
| `package-service.md` | 949 | "setting all tracks to final status" | Test description |
| `product-lifecycle-transitions.md` | 128 | "tracks with a final status" | EOL handling — tracks in final status not modified |
| `ibs-submission-tracking.md` | 861-862 | "non-final statuses" / "final statuses" | Display relevance for TicketPackageTrack statuses |
| `ibs-rabbitmq-integration.md` | 390 | "in a final status" | Packages in a final TicketPackageTrack status |
| `architecture.md` | 271 | "records in a final status" | Release tracking — track-level detection |
| `tickets.md` | 341 | "from a final state to AFFECTED" | VA resets a **track** status from final to AFFECTED |
| `data-model.md` | 866 | "final/non-final classification" | Cross-reference to package-model.md |
| `reviews/package-service.md` | 13 | "Final-status protection" | Review finding about TicketPackageTrack |
| `reviews/package-model.md` | 37 | "other final statuses" | Review finding about TicketPackageTrack |

#### "Non-final" — TicketPackageTrack domain

| File | Line | Term | Context |
|------|------|------|---------|
| `package-model.md` | 312-313, 317 | "non-final" / "Non-final statuses" | Canonical definition |
| `package-service.md` | 953 | "non-final status" | Test description for backward transitions |
| `product-lifecycle-transitions.md` | 6, 121 | "non-final" | Scope: non-final TicketPackageProduct/track records |
| `ibs-submission-tracking.md` | 861 | "non-final statuses" | Display relevance for TicketPackageTrack |
| `data-model.md` | 866 | "final/non-final" | Cross-reference to package-model.md |
| `open-points.md` | 142, 144, 148 | "final-status tracks" / "non-final tracks" | TicketPackageTrack scope filtering |

#### "Final state" / "non-final" — IBS request lifecycle

| File | Line | Term | Context |
|------|------|------|---------|
| `ibs-submission-tracking.md` | 95 | "NOT a final state" | IBS `declined` state definition |
| `ibs-submission-tracking.md` | 113 | "non-final" | IBS PackageStatus hypothesis |
| `package-model.md` | 616 | "negative final state" | IBS SubmissionRequest states |
| `package-model.md` | 622 | "terminal or non-final state" | IBS submission regression |
| `package-model.md` | 641 | "final state for release" | IBS `accepted` is irreversible |
| `data-model.md` | 1448 | "`declined` is non-final" | SubmissionRequestState enum |
| `data-model.md` | 1467 | "`declined` is non-final" | ReleaseRequestState enum |
| `maintainer.md` | 71, 250 | "non-final or progressing state" / "final state" | SubmissionRequest lifecycle |

#### "Final state" — Other domains

| File | Line | Term | Domain |
|------|------|------|--------|
| `fetcher-infrastructure.md` | 75 | "final status" | Fetcher execution outcome |
| `user-service.md` | 795 | "same final state" | User role operations idempotency |
| `ad-integration.md` | 454 | "same final state" | LDAP sync idempotency |
| `cve-tracking.md` | 1194, 1248, 1251 | "final states" | CVESource fetch status |

#### "Terminal state" — User lifecycle domain

| File | Line | Term | Context |
|------|------|------|---------|
| `user-service.md` | 139 | "terminal state" | Deactivation is irreversible end state of user lifecycle |

#### "Open" — Non-ticket-status domains

| File | Line | Term | Context |
|------|------|------|---------|
| `tickets.md` | 1522 | "Reopen Ticket" | Status transition action (API endpoint name), not a status category |
| `ibs-submission-tracking.md` | 680 | "SR is in open or accepted state" | IBS request state, not ticket status |
| `ibs-submission-tracking.md` | 1004 | "open SubmissionRequest/ReleaseRequest records" | IBS entity state, not ticket status |
| `package-model.md` | 616 | "in `open` or `accepted` state" | IBS SubmissionRequest states |
| `data-model.md` | 1448 | "`open` maps to IBS states" | SubmissionRequestState enum value |

#### "Closed" — Non-ticket-status domains

| File | Line | Term | Context |
|------|------|------|---------|
| `data-model.md` | 466 | "closed value set" | PostgreSQL ENUM type concept (finite set) |
| `ibs-product-release-detection.md` | 283 | "will be closed" | Spec open items to be addressed later |
| `package-model.md` | 481 | "incident closed" | IBS incident lifecycle |
| `ibs-rabbitmq-integration.md` | 119 | "close the connection" | Network connection shutdown |

## Implementation Plan

### Phase 1: Add terminology convention to `conventions.md`

Add a new "Ticket Status Category Terminology" subsection under the
General section (after "Cascade / Chain / Flattening Terminology"),
establishing the naming rule:

- Canonical terms: "active status" for `{New, Analysis, Analyzed}`,
  "inactive status" for `{Resolved, Ignored, Duplicated}`
- Forbidden alternatives: "terminal status", "terminal state",
  "non-active state", "non-active tickets", "open tickets" / "open
  status" (when referring to the active category), "closed" / "closure" /
  "auto-closed" / "manually-closed" (when referring to the inactive
  category), "final status" (when referring to ticket statuses — "final
  status" is reserved for `TicketPackageTrack` statuses per
  `package-model.md`)
- Reference `docs/features/tickets/tickets.md` (Status Categories) as
  the authoritative definition of which statuses compose each set
- **Disambiguation scope**: the convention applies exclusively to
  **ticket** status categories. The following unrelated uses are NOT
  affected:
  - User lifecycle: `User.active` field, "inactive user", "active
    status" as a boolean attribute (identity domain)
  - Product lifecycle: `Product.active` field (product domain)
  - Assignee state: "inactive assignee" = user whose `active` field
    is `false` (ticket-mutations domain)
  - IBS request states: "open", "accepted", "declined" (IBS domain)
  - IBS incident lifecycle: "incident closed" (IBS domain)
  - Status transition verbs: "Reopen" (action, not state category)

Rationale for placement: `conventions.md` owns the "which term to use"
rule (writing convention). `tickets.md` owns the "what it means
operationally" definition (domain knowledge). This avoids duplicating
the status list in two places — if the set changes, only `tickets.md`
is updated.

### Phase 2: Update `tickets.md`

- Rename section heading at line 699:
  `## Terminal Statuses and Mutability` → `## Inactive Statuses and Mutability`
- Replace at line 452:
  "a non-final ticket" → "an active ticket"

### Phase 3: Update `ticket-mutations.md`

Replace four occurrences:

- Line 248: "the resulting status is non-final (Analysis or Analyzed)" →
  "the resulting status is active (Analysis or Analyzed)"
- Line 261: "If the resulting status is final (Resolved, Ignored,
  Duplicated)" → "If the resulting status is inactive (Resolved, Ignored,
  Duplicated)"
- Line 262: "the ticket is closed and does not need an active assignee" →
  "an inactive ticket does not need an active assignee"
- Line 357: `"""Reject mutations on manually-closed tickets.` →
  `"""Reject mutations on manual-zone inactive tickets.`

### Phase 4: Update `data-model.md`

Replace two occurrences:

- Line 718: "For resolved or inactive tickets, the score" →
  "For inactive tickets, the score" (tautology fix — "resolved" is
  already a member of the inactive set)
- Line 720: "non-active tickets" → "inactive tickets"

### Phase 5: Update `ticket-references.md`

Replace all 4 occurrences of "final status" (lines 387, 408, 409, 413)
when they refer to the ticket inactive status set
`{Resolved, Ignored, Duplicated}`:

- Line 387: "in a final status (Resolved, Ignored)" →
  "in an inactive status (Resolved, Ignored, Duplicated)"
- Line 408: "Tickets in final status" →
  "Tickets in inactive statuses"
- Line 409: "tickets in final status (Resolved, Ignored, Duplicated)" →
  "tickets in inactive statuses (Resolved, Ignored, Duplicated)"
- Line 413: "tickets in final status" →
  "tickets in inactive statuses"

### Phase 6: Update `cve-tracking.md`

Replace two occurrences:

- Line 334: "Automatic closure is only safe for tickets that no one has
  started working" → "Automatic transition to Ignored is only safe for
  tickets that no one has started working"
- Line 339: "auto-closed by the original rejection" →
  "automatically set to Ignored by the original rejection"

### Phase 7: Update `user-service.md`

Replace two occurrences:

- Line 199: "they are closed and do not need an active assignee" →
  "they no longer need an active assignee"
- Line 594: "Unassign open tickets: call" →
  "Unassign active tickets: call"

### Phase 8: Update `user-management.md`

Replace three occurrences:

- Line 276: "Count of open tickets assigned to the user that will be
  unassigned" → "Count of active tickets assigned to the user that will
  be unassigned"
- Line 283: "{n} open tickets will be unassigned" →
  "{n} active tickets will be unassigned"
- Line 870: "Open tickets assigned to this user that will be unassigned" →
  "Active tickets assigned to this user that will be unassigned"

### Phase 9: Update `package-model.md`

Replace one occurrence:

- Line 520: "in open tickets with" → "in active tickets with"

### Phase 10: Update `product-catalog.md`

Replace one occurrence:

- Line 146: "open tickets referencing that product" →
  "active tickets referencing that product"

### Phase 11: Update `product-lifecycle-transitions.md`

Replace three occurrences:

- Line 109: "referencing this product in open" →
  "referencing this product in active"
- Line 120: "referencing this product in open" →
  "referencing this product in active"
- Line 133: "referencing this product in open" →
  "referencing this product in active"

### Phase 12: Update `ibs-submission-tracking.md`

Replace one occurrence:

- Line 674: "the parent ticket is in an open state" →
  "the parent ticket is in an active status"

### Phase 13: Update `open-points.md`

Replace at line 199:
"tickets in non-final status" → "tickets in active statuses"

### Phase 14: Update `reviews/system-settings.md`

Replace one occurrence:

- Line 45: "open tickets will be re-evaluated" →
  "active tickets will be re-evaluated"

Note: this file is in `docs/reviews/` (untracked by git). Update if
the file exists on disk.

### Phase 15: Verify no remaining occurrences

Run a repo-wide search in documentation files for:

- "terminal status" / "terminal state" (in ticket contexts)
- "non-active" (in ticket contexts)
- "non-final" (in ticket contexts — NOT TicketPackageTrack/IBS)
- "open tickets" / "open ticket" / "open state" (in ticket contexts —
  NOT IBS request states)
- "closed" / "closure" / "auto-closed" / "manually-closed" (in ticket
  contexts — NOT IBS incident, network connections, enum type concepts)
- "final status" (in ticket contexts — NOT TicketPackageTrack/IBS/fetcher/
  user lifecycle/CVE source)

Cross-reference each hit against the "False positives excluded" table
above. Any hit NOT listed in that table and NOT already addressed in
Phases 2-14 is a missed occurrence that must be fixed.

### Phase 16: Run reviewers

After all textual changes are applied, run the following reviewers to
verify consistency:

- `@spec-coherence-reviewer` on `tickets.md` and `ticket-mutations.md`
  — verify the changes do not introduce inconsistencies with other specs
- `@docs-reviewer` on `data-model.md` and `ticket-references.md` —
  verify documentation remains coherent after terminology updates
- `@docs-placement-reviewer` on `conventions.md` — verify the new
  terminology section is correctly placed

### Phase 17: Remove draft file

Delete `docs/drafts/standardize-inactive-status-terminology.md` — the
work is complete and the convention is codified in `conventions.md`.

## Change Summary

| Category | Occurrences | Files |
|----------|-------------|-------|
| Group A: terminal/non-final/final → inactive/active | 10 | 5 files |
| Group B: open → active | 11 | 7 files |
| Group C: closed/closure/auto-closed → inactive/explicit | 5 | 3 files |
| Group D: tautology fix | 1 | 1 file |
| **Total changes** | **27** | **13 unique files** |

## Cross-References

- `docs/conventions.md` — terminology rule (canonical term, forbidden
  alternatives, disambiguation scope)
- `docs/features/tickets/tickets.md` — authoritative definition of
  status categories (which statuses compose each set); also contains
  occurrences to fix (lines 452, 699)
- `docs/features/tickets/ticket-mutations.md` — uses "non-final",
  "final", "closed", "manually-closed" (lines 248, 261, 262, 357)
- `docs/features/tickets/ticket-references.md` — uses "final status"
  for ticket statuses (lines 387, 408, 409, 413)
- `docs/features/tickets/cve-tracking.md` — uses "closure",
  "auto-closed" (lines 334, 339)
- `docs/features/tickets/ticket-service.md` — already aligned
- `docs/features/tickets/cvss-scoring.md` — already aligned
- `docs/features/platform/fetcher-infrastructure.md` — already aligned
- `docs/features/identity/user-service.md` — uses "closed", "open
  tickets" (lines 199, 594)
- `docs/features/identity/user-management.md` — uses "open tickets"
  (lines 276, 283, 870)
- `docs/features/packages/package-model.md` — uses "open tickets"
  (line 520)
- `docs/features/packages/product-catalog.md` — uses "open tickets"
  (line 146)
- `docs/features/packages/product-lifecycle-transitions.md` — uses
  "open" for ticket status (lines 109, 120, 133)
- `docs/features/packages/ibs-submission-tracking.md` — uses "open
  state" for ticket status (line 674)
- `docs/data-model.md` — uses "non-active tickets", "resolved or
  inactive" tautology (lines 718, 720)
- `docs/drafts/open-points.md` — uses "non-final status" for tickets
  (line 199)
- `docs/reviews/system-settings.md` — uses "open tickets" (line 45)
