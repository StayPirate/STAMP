# Remove "Protected State" Concept from WONT_FIX

**Status**: Draft (rev 2 — post-review)  
**Created**: 2026-05-22  
**Last updated**: 2026-05-22  
**Scope**: Remove all references to "protected state" from specifications;
decouple product-level release detection scanning scope from affectedness
status and eligibility

## Motivation

The `WONT_FIX` status is currently described across specifications as a
"protected state" — a special designation meaning it is immune to all
automatic/system-initiated status transitions. This concept was introduced
early in the project to ensure VA decisions are preserved, but it creates an
unnecessary distinction between `WONT_FIX` and the other two final statuses
(`NOT_AFFECTED`, `FIXED`).

In practice, `WONT_FIX` does not need special protection because the automatic
transition rules already define their valid source statuses explicitly:

- Track release detection: `AFFECTED` or `ANALYSIS` → `FIXED`
- Product status propagation: inherits from track (only non-override products)

Neither rule lists `WONT_FIX` as a valid source status — so it would never be
modified anyway, **for the same reason `NOT_AFFECTED` is never modified**. The
"protected" concept is redundant and misleading: it implies `WONT_FIX` has a
special immunity mechanism when in reality it is simply not an eligible source
state.

Additionally, the concept introduces an incoherence in some specs (e.g.,
`ibs-track-release-detection.md` says "unless current status is WONT_FIX"
instead of the canonical "only when status is AFFECTED or ANALYSIS"), which
could lead to implementation bugs if a developer reads the detector spec
without consulting the transitions table.

## Decisions

### Decision 1 — Remove "protected state" terminology

`WONT_FIX` is a final status that is not modified by automatic transitions
because it is not in the set of valid source statuses — identical to
`NOT_AFFECTED` and `FIXED`. All references to "protected state" are removed.
Downstream specs reference the canonical final status definition in
`package-model.md` rather than enumerating all three values each time.

**Nature**: documentation-only cleanup, no behavioral change.

### Decision 2 — Decouple product-level release detection from status/eligibility

The product-level release detection scanner currently filters by
`eligible = true` and excludes `WONT_FIX` products. Both filters are removed.

The scanning scope becomes: all `TicketPackageProduct` records with
`released_at IS NULL` belonging to active tickets.

**Rationale**: `released_at`, `eligible`, and `status` (affectedness) are three
orthogonal dimensions:

| Dimension | Meaning | Set by |
|-----------|---------|--------|
| `status` | Is this product affected by the vulnerability? | VA decision or track propagation |
| `eligible` | Does the CVSS score meet the product's threshold? | System (CVSS recalculation) |
| `released_at` | Has a fix been published in the product's update repo? | System (product release detector) |

Recording `released_at` is a factual observation ("an advisory referencing
this CVE exists in the product repository"). It does not override the VA's
affectedness decision and should not be gated by it. A product marked
`WONT_FIX` that receives a release is a valid real-world scenario (e.g.,
the fix was bundled in another update) — recording the timestamp is correct.

**Nature**: behavioral change (intentional design correction). Products
previously excluded from scanning (`WONT_FIX`, or those with
`eligible = false`) will now be scanned and may receive `released_at`.

## Current State

### Where "protected state" appears (15 locations, 10 files)

| # | File | Lines | Usage |
|---|------|-------|-------|
| 1 | `docs/architecture.md` | 266 | "Both levels honor the protected state `WONT_FIX`" |
| 2 | `docs/features/packages/package-model.md` | ~616-620 | Dedicated "Protected state" paragraph in Status Behavior |
| 3 | `docs/features/packages/package-model.md` | ~1084 | Parenthetical "(protected state)" in Release Tracking |
| 4 | `docs/features/packages/ibs-track-release-detection.md` | 35-37 | Suppression paragraph referencing protected state |
| 5 | `docs/features/packages/ibs-track-release-detection.md` | 132 | "unless current status is `WONT_FIX`" |
| 6 | `docs/features/packages/ibs-product-release-detection.md` | 30-32 | Exclusion with "(protected state — see ...)" |
| 7 | `docs/features/packages/ibs-product-release-detection.md` | 236-237 | "(protected state)" parenthetical |
| 8 | `docs/features/packages/ibs-product-release-detection.md` | 260-261 | "(protected state)" in scope description |
| 9 | `docs/features/integrations/ibs-rabbitmq-integration.md` | 181 | "unless protected status `WONT_FIX`" |
| 10 | `docs/features/integrations/ibs-integration.md` | 243-244 | "never modifies records with protected status (`WONT_FIX`)" |
| 11 | `docs/features/tickets/cvss-scoring.md` | 347 | "Products in the protected state `WONT_FIX` are not modified" |
| 12 | `docs/features/packages/product-lifecycle-transitions.md` | 106-109 | Note about additional protection |
| 13 | `docs/features/packages/product-lifecycle-transitions.md` | 167-173 | Dedicated "## Protected States" section |
| 14 | `docs/drafts/open-points.md` | 134-182 | Section 4 proposing removal |
| 15 | `docs/reviews/package-model.md` | 35, 40 | Gap analysis referencing protected state |

### What does NOT exist (confirming no code impact for Decision 1)

- No `PROTECTED_STATUSES` constant anywhere in `backend/` or `frontend/`
- No `is_protected()` function or method
- No Python/TypeScript logic implementing the "protected" concept
- No tests referencing "protected status"

## Action Plan

### Part A — Remove "protected state" terminology (Decision 1)

#### Step A1 — `docs/features/packages/package-model.md`

This is the authoritative source for the final status definition.

1. **Remove the "Protected state" paragraph** (~lines 616-620). The automatic
   transitions table already defines valid source statuses — no additional
   paragraph is needed.

2. **Remove "(protected state)" parenthetical** (~line 1084) in the Release
   Tracking section. The text should simply state that only records in
   `AFFECTED` or `ANALYSIS` status are eligible for automatic transition.

#### Step A2 — `docs/features/packages/ibs-track-release-detection.md`

1. **Lines 35-37**: remove the suppression paragraph. Replace with: "The
   automatic transition applies only when the current track status is
   `AFFECTED` or `ANALYSIS` (see `package-model.md`, Automatic Transitions)."

2. **Line 132**: change "unless current status is `WONT_FIX`" to "only when
   current status is `AFFECTED` or `ANALYSIS`".

#### Step A3 — `docs/features/integrations/ibs-rabbitmq-integration.md`

**Line 181**: change "unless protected status `WONT_FIX`" to "only when current
status is `AFFECTED` or `ANALYSIS`".

#### Step A4 — `docs/features/integrations/ibs-integration.md`

**Lines 243-244**: change "never modifies records with protected status
(`WONT_FIX`)" to "only modifies records with status `AFFECTED` or `ANALYSIS`".

#### Step A5 — `docs/features/tickets/cvss-scoring.md`

**Line 347**: change "Products in the protected state `WONT_FIX` are not
modified" to "Products in a final status are not modified" (referencing
the canonical definition in `package-model.md`).

#### Step A6 — `docs/features/packages/product-lifecycle-transitions.md`

1. **Lines 106-109**: remove the note "WONT_FIX is additionally protected from
   automatic transitions". The preceding sentence already states that records
   in final status are not modified.

2. **Lines 167-173**: remove the "## Protected States" section entirely. The
   existing final status language in the preceding sections is sufficient.

#### Step A7 — `docs/architecture.md`

**Line 266**: change "Both levels honor the protected state `WONT_FIX`, which
is never modified automatically" to "Both levels only transition records in
`AFFECTED` or `ANALYSIS` status; records in a final status are not modified."

#### Step A8 — `docs/drafts/open-points.md`

**Remove section 4 entirely** (lines 134-182). The decision has been made.

#### Step A9 — `docs/reviews/package-model.md`

**Remove or resolve PKM-GAP-004** (lines ~35, 40). The gap referenced the
"protected state" interaction which no longer exists as a concept.

### Part B — Decouple product release detection scope (Decision 2)

#### Step B1 — `docs/features/packages/ibs-product-release-detection.md`

This is the main change. The scanning scope definition must be updated:

1. **Lines 30-32** (current: WONT_FIX exclusion): remove entirely. The scope
   is now defined purely by `released_at IS NULL` on active tickets.

2. **Lines 236-237** (outcome table referencing WONT_FIX exclusion): remove
   the row or note about WONT_FIX being excluded.

3. **Lines 260-261** (scope description with `eligible = true` and WONT_FIX
   exclusion): rewrite scope as: "Scans all `TicketPackageProduct` records
   with `released_at IS NULL` belonging to active tickets. Soft-deleted
   products are included (see hierarchical exclusion model)."

4. **Any filter conditions** referencing `eligible = true`: remove. The
   scanner does not gate on eligibility.

#### Step B2 — `docs/features/packages/package-model.md`

If the Release Tracking section references product-level scanning scope with
eligibility or status filters, update to reflect the new scope (purely
`released_at IS NULL` on active tickets).

#### Step B3 — `docs/architecture.md`

If the Release Tracking Flow section (lines ~258-268) mentions eligibility-
based filtering for product-level detection, update to match.

## Verification

After all edits:

1. Search the entire repository for "protected state" — expect zero results
   in any specification
2. Search for "protected" near "WONT_FIX" or "wont_fix" — expect zero results
3. Verify that every mention of automatic status transitions uses positive
   language ("only when status is X or Y") rather than negative exclusions
   ("unless status is WONT_FIX")
4. Verify that `ibs-product-release-detection.md` scanning scope has no
   filters on `status` or `eligible`
5. Run `@spec-coherence-reviewer` on modified specs to confirm consistency

## Risk Assessment

### Decision 1 — Remove "protected state" (Very Low risk)

- No behavioral change — `WONT_FIX` was never going to be modified anyway
- No code exists implementing the concept
- The change makes specifications more consistent and easier to implement

### Decision 2 — Decouple product scanner scope (Low risk)

- Behavioral change: products previously excluded (`WONT_FIX`, ineligible)
  will now be scanned
- Impact is positive: more complete data (`released_at` recorded for all
  products that receive updates)
- No VA decisions are overridden (only a timestamp is set, not status)
- Slightly more scanning work (negligible — WONT_FIX and ineligible products
  are a small subset)
- No code exists yet — change applies at spec level before implementation
