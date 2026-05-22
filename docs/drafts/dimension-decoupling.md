# Dimension Decoupling: Affectedness, Eligibility, Delivery

> **Status**: Draft — work in progress across sessions
> **Goal**: Identify and remove unnecessary couplings between the three
> orthogonal dimensions of the package tracking model, making each
> dimension independently computable and modifiable.
> **Cleanup**: Delete this file once all changes are implemented and
> reviewed.

## Background

The package tracking model defines three orthogonal axes for every
`TicketPackageProduct` (and partially for `TicketPackageTrack`):

1. **Affectedness** — is the source code vulnerable? (`PackageStatus` enum)
2. **Eligibility** — does the product meet the criteria for receiving a
   fix? (`eligible` boolean)
3. **Delivery** — has the fix been distributed? (`delivery_status` on
   tracks, `released_at` on products)

These dimensions are declared orthogonal in `package-model.md` (Design
Decision 6), but the current specifications contain multiple coupling
points where one dimension's behavior depends on the state of another.
This document catalogs every coupling, classifies it, and defines
concrete actions to decouple where appropriate.

---

## Coupling Inventory

### Legend

| Verdict | Meaning |
|---------|---------|
| **KEEP** | The coupling is inherent to the business logic; removing it would break correctness |
| **REMOVE** | The coupling can and should be removed to improve orthogonality |
| **SIMPLIFY** | The coupling should be restructured to reduce cross-dimensional dependency |

---

### C1: Resolved Gate — all three dimensions combined

- **Location**: `docs/features/tickets/tickets.md:328-337`
- **Dimensions**: Affectedness + Eligibility + Delivery
- **Description**: The Analyzed-to-Resolved gate requires: (1) every
  active track has a final affectedness status (`FIXED`, `NOT_AFFECTED`,
  `WONT_FIX`), and (2) every eligible product (`eligible = true`) under a
  `FIXED` track has `released_at IS NOT NULL`.
- **Verdict**: **KEEP**
- **Rationale**: The resolution gate is the business definition of "done".
  It must combine all three dimensions because "resolved" inherently means
  all affected code is fixed, all eligible products received the fix, and
  the fix was delivered. This is not an accidental coupling — it is the
  purpose of the gate.
- **Action**: None. The gate logic does not change.

---

### C2: Eligibility evaluated only when status is AFFECTED

- **Location**: `docs/features/packages/package-model.md:335-338`,
  `:836-842`
- **Dimensions**: Affectedness → Eligibility
- **Description**: Eligibility is calculated and stored only when a
  product's status is or becomes `AFFECTED`. For all other statuses,
  eligibility is not evaluated.
- **Verdict**: **REMOVE**
- **Rationale**: Eligibility is a property of the product relative to the
  CVE (CVSS score vs. product threshold + lifecycle phase). It does not
  logically depend on whether the code is vulnerable. Decoupling makes
  eligibility a pure, independently computable fact: "does this product
  meet the criteria for receiving a fix?"
- **New semantics**: `eligible = true` means `CVSS >= threshold AND NOT
  in Reactive LTSS phase`. This is true regardless of affectedness status.
  A product in `NOT_AFFECTED` with `eligible = true` simply means "the
  product meets the threshold criteria" — the Resolved gate already
  correctly scopes its check to products under `FIXED` tracks, so this
  does not change gate behavior.
- **Cascading impacts**: see [C2 Impact Analysis](#c2-impact-analysis)
  section below.

---

### C3: Override reset conditioned on AFFECTED

- **Location**: `docs/features/packages/package-model.md:1655-1658`
- **Dimensions**: Affectedness → Eligibility
- **Description**: When a VA resets an eligibility override (`eligible:
  null`), recalculation applies only if the product's status is `AFFECTED`;
  otherwise `eligible` is set to `false`.
- **Verdict**: **REMOVE** (consequence of C2)
- **Rationale**: With C2 removed, eligibility is always recalculated
  regardless of status.
- **Action**: Remove the `if AFFECTED` condition. On override reset,
  always recalculate using the standard rules (CVSS threshold + lifecycle
  phase).

---

### C4: Record creation eligibility conditioned on parent AFFECTED

- **Location**: `docs/features/packages/package-service.md:699-715`
- **Dimensions**: Affectedness → Eligibility
- **Description**: When creating a `TicketPackageProduct`, eligibility is
  calculated only if the parent track is `AFFECTED`.
- **Verdict**: **REMOVE** (consequence of C2)
- **Rationale**: With C2 removed, eligibility is always calculated at
  creation time regardless of parent track status.
- **Action**: Calculate eligibility for all new products during record
  creation, regardless of parent track status.

---

### C5: `delivery_status != RELEASED` in track release detection scope

- **Location**: `docs/features/packages/ibs-track-release-detection.md:83-89`,
  `:203-208`
- **Dimensions**: Delivery → Affectedness (scope filter)
- **Description**: The periodic track release detection query filters
  active codestreams by `status in (ANALYSIS, AFFECTED) AND
  delivery_status != RELEASED`. The `delivery_status` filter is an
  optimization to avoid re-checking tracks where delivery is confirmed.
- **Verdict**: **REMOVE**
- **Rationale**: This filter masks the anomaly `AFFECTED + RELEASED`. If
  a track is `RELEASED` but not `FIXED` (anomalous state), the detection
  process should still be able to transition it to `FIXED`. The cost of
  the extra queries is negligible (tracks with `RELEASED` that are also
  `FIXED` are already excluded by the `status in (ANALYSIS, AFFECTED)`
  filter). Removing the `delivery_status` filter leaves only the
  affectedness-based scope, which is intra-dimensional and correct.
- **Action**: Remove `delivery_status != RELEASED` from the scope query
  in both the Procedure section (line 85) and the Background Task
  section (line 206).

---

### C6: SR processing filters by affectedness status (Pipelines 1 & 2)

- **Location**: `docs/features/packages/ibs-submission-tracking.md:486-489`,
  `:598-601`
- **Dimensions**: Affectedness → Delivery (scope filter)
- **Description**: Pipelines 1 (real-time) and 2 (catch-up) filter SR
  processing to codestreams with at least one track in `ANALYSIS` or
  `AFFECTED` status. Tracks in final affectedness statuses are excluded
  from SR discovery.
- **Verdict**: **REMOVE**
- **Rationale**: Pipeline 3 (retroactive discovery,
  `ibs-submission-tracking.md:736-741`) explicitly has NO status filter
  and includes tracks in all statuses. This inconsistency means that SRs
  discovered retroactively cover all statuses while real-time and catch-up
  monitoring ignores final-status tracks. The result: transitioning a
  track from `AFFECTED` to `NOT_AFFECTED` while an SR is in progress
  could cause missed SR state updates. SR tracking is a factual
  observation — it should not depend on affectedness state.
- **Action**: Remove the affectedness status filter from Pipeline 1
  (line 488) and Pipeline 2 (line 600). SR/RR events are processed for
  all tracked codestreams regardless of track affectedness status. The
  scope is bounded to codestreams from active tickets (ticket status in
  New/Analysis/Analyzed, ticket `deleted_at IS NULL`).
- **Performance note**: for a well-analyzed ticket with N tracks and K
  non-final, the monitored codestream count expands from K to N (typical
  ratio: 10-25x for tickets where K=1-2, N=20-50). The cost is limited
  to initial event filtering and task enqueue — `correlate_submission_request`
  already checks package relevance before performing any IBS API calls.

---

### C7: RabbitMQ consumer codestream filter uses affectedness

- **Location**: `docs/features/integrations/ibs-rabbitmq-integration.md:148-153`
- **Dimensions**: Affectedness → Codestream monitoring scope
- **Description**: The `IBSEventConsumer` builds the set of monitored
  codestreams from tracks with `status in (ANALYSIS, AFFECTED)`.
- **Verdict**: **KEEP** (for `package.commit` events — release detection),
  **REMOVE** (for `request.create` / `request.state_change` events — SR
  tracking)
- **Rationale**: For release detection (`package.commit` events), the
  affectedness filter is correct: there is no point detecting a fix for
  tracks already in a final state (`FIXED`, `NOT_AFFECTED`, `WONT_FIX`).
  This is intra-dimensional (Affectedness constraining Affectedness scope).
  The automatic transition table (`package-model.md:611-617`) explicitly
  states that records in a final status are not eligible as source states
  for automatic transitions — so even if a fix were detected for a
  `NOT_AFFECTED` or `WONT_FIX` track, no action would be taken.
  Monitoring those codestreams would be wasted work. If in the future
  the anomaly observer needs to detect fixes present in codestreams where
  the track is `NOT_AFFECTED` or `WONT_FIX`, the IBS consumer and the
  `check_ibs_track_releases` fetcher would need to be extended to also
  scan final-status tracks — but that is a responsibility of the anomaly
  observer feature, not of the release detector.
  For SR/RR events (`request.create`, `request.state_change`), the
  filter should be removed per C6.
- **Action**: Replace the current single filtered set with a single
  **metadata-enriched set** (`Dict[codestream_name, has_non_final_tracks: bool]`):
  - **Scope**: all codestreams from active tickets (ticket status in
    New/Analysis/Analyzed, ticket `deleted_at IS NULL`), regardless of
    track affectedness status.
  - **Metadata**: `has_non_final_tracks = true` if the codestream has at
    least one track in `ANALYSIS` or `AFFECTED`.
  - **Refresh**: every 5 minutes (unchanged), single DB query builds the
    entire dict atomically.
  - **Processing rules**:
    - `package.commit` events: check `project in set AND
      set[project].has_non_final_tracks == true`. If false → discard.
    - `request.create` / `request.state_change` events: check only
      `project in set`. No additional filter.
  - This eliminates the need for dual codestream sets and the associated
    refresh synchronization risk.

---

### C8: `delivery_relevant` computed field

- **Location**: `docs/features/packages/package-model.md:396-429`
- **Dimensions**: Affectedness + Delivery (API presentation)
- **Description**: `delivery_relevant` is a boolean computed from
  `status` and `delivery_status`, returned in API responses to help
  clients distinguish meaningful delivery states from default noise.
- **Verdict**: **KEEP**
- **Rationale**: This is a read-only API convenience field with no
  behavioral side effects. It does not modify either dimension. It exists
  to improve UX by signaling when delivery information is meaningful.
  Moving this computation to the frontend would add business logic to the
  client without meaningful benefit. The coupling is purely presentational.
- **Action**: None. The field remains as-is.

---

### C9: Anomaly matrix (Affectedness x Delivery)

- **Location**: `docs/features/packages/package-model.md:523-558`
- **Dimensions**: Affectedness + Delivery (observational)
- **Description**: Five anomalous combinations are defined by crossing
  affectedness and delivery states (e.g., `AFFECTED + RELEASED`,
  `NOT_AFFECTED + IN_PROGRESS`).
- **Verdict**: **KEEP**
- **Rationale**: Anomaly detection is inherently cross-dimensional — the
  anomaly is defined by the combination. This is not a coupling in the
  operational sense (no dimension constrains or modifies the other). It is
  an observation that certain combinations signal problems. See the
  [Anomaly Observer](#anomaly-observer) section for the proposed
  decoupled implementation.
- **Action**: None to the specification. The future Review Queue should
  implement anomaly detection as an independent observer (see below).

---

### C10: Maintainer view sections combine Affectedness + Delivery

- **Location**: `docs/features/packages/maintainer.md:49-82`
- **Dimensions**: Affectedness + Delivery (UX presentation)
- **Description**: The maintainer task view organizes tracks into
  Pending / In Progress / Completed sections based on combining
  affectedness (`AFFECTED`) with SR/RR state (delivery).
- **Verdict**: **KEEP**
- **Rationale**: The maintainer view answers "what do I need to work on?"
  which inherently requires combining both dimensions. A maintainer needs
  to know both that a track is affected (there is work to do) and what
  the delivery status is (whether an SR has been submitted). This is a
  presentation-level combination, not an operational coupling.
- **Action**: None.

---

### C11: Submission chain relevance for non-final tracks

- **Location**: `docs/features/packages/ibs-submission-tracking.md:853`
- **Dimensions**: Affectedness → Delivery (presentation)
- **Description**: The submission chain is described as "relevant" only
  for non-final affectedness statuses.
- **Verdict**: **SIMPLIFY**
- **Rationale**: With C6 removing the affectedness filter from SR
  processing, submission data will exist for tracks in all statuses. The
  "relevance" label becomes a presentation concern, not a processing
  constraint. The chain should be tracked for all tracks and displayed
  with appropriate emphasis based on affectedness state.
- **Action**: Update the spec to say the chain is always tracked. Display
  relevance is determined by the frontend (similar to `delivery_relevant`
  for C8).

---

### C12: EOL soft-deletes only AFFECTED/ANALYSIS products

- **Location**: `docs/features/packages/product-lifecycle-transitions.md:95-106`
- **Dimensions**: Affectedness → Lifecycle action
- **Description**: When a product reaches EOL, only products in
  `AFFECTED` or `ANALYSIS` status are soft-deleted. Products in final
  statuses (`NOT_AFFECTED`, `FIXED`, `WONT_FIX`) are preserved.
- **Verdict**: **KEEP**
- **Rationale**: Products in final statuses represent completed decisions
  or historical records. Soft-deleting them would lose information about
  past analysis. The coupling is justified: the action (soft-delete) is
  appropriate only for records that are still "in progress" — which is
  defined by affectedness status. This is not a cross-dimensional coupling
  (it does not involve eligibility or delivery).
- **Action**: None.

---

### C13: Git track release detection sets both status and delivery_status

- **Location**: `docs/features/packages/git-track-release-detection.md:14`
- **Dimensions**: Affectedness + Delivery (simultaneous mutation)
- **Description**: Git release detection sets `status = FIXED` and
  `delivery_status = RELEASED` in a single operation, unlike IBS where
  these are managed by independent mechanisms.
- **Verdict**: **SIMPLIFY**
- **Rationale**: For git-based tracks, the fix landing in the repository
  IS the delivery event (there is no separate SR/RR workflow). However,
  the implementation should still call two distinct service functions
  (`set_track_status(FIXED)` and `set_track_delivery_status(RELEASED)`)
  to maintain: (a) separate audit trail events for each dimension, (b)
  consistency with the IBS model, and (c) the ability to independently
  test each transition.
- **Action**: Update the git track release detection spec to call two
  service functions sequentially instead of setting both fields in a
  single operation. The trigger is one event, but the mutations are two
  distinct operations. Both calls execute within a single database
  transaction owned by the caller (git release detector). If either
  fails, the entire transaction rolls back — the track never reaches an
  inconsistent state where only one dimension is updated.

---

### C14: CVSS cascade skips products in final affectedness status

- **Location**: `docs/features/tickets/cvss-scoring.md:347-348`
- **Dimensions**: Affectedness → Eligibility (cascade filter)
- **Description**: The CVSS recalculation cascade skips products in final
  affectedness statuses (`NOT_AFFECTED`, `FIXED`, `WONT_FIX`).
- **Verdict**: **REMOVE** (consequence of C2)
- **Rationale**: With C2 removed, eligibility is always recalculated
  regardless of affectedness status. A CVSS score change should update
  eligibility for all products.
- **Action**: Remove the final-status filter from the recalculation
  cascade step 2.

---

### C15: `set_track_delivery_status()` calls `evaluate_ticket_status()`

- **Location**: `docs/features/packages/package-service.md:187-189`
- **Dimensions**: Delivery → Ticket status evaluation
- **Description**: Changing `delivery_status` on a track triggers ticket
  status re-evaluation, even though `delivery_status` is not part of any
  gate condition (only `released_at` on products is).
- **Verdict**: **REMOVE**
- **Rationale**: The call is a no-op today (`delivery_status` is not in
  any gate), but it can mask bugs elsewhere. Scenario: a function modifies
  a track's affectedness status but forgets to call
  `evaluate_ticket_status()`. The ticket is left in an inconsistent state.
  Later, `set_track_delivery_status()` is called on the same track for an
  unrelated reason. Its `evaluate_ticket_status()` call silently picks up
  the earlier affectedness change and corrects the ticket status. The bug
  is now hidden — the ticket self-corrects non-deterministically,
  depending on whether a delivery update happens to arrive. This makes the
  original bug intermittent and difficult to reproduce. If
  `delivery_status` ever enters a gate condition in the future, the call
  can be reintroduced at that time (YAGNI).
- **Action**: Remove the `evaluate_ticket_status()` call from
  `set_track_delivery_status()` in `package-service.md`.

---

## C2 Impact Analysis

Removing the C2 coupling (eligibility conditioned on `AFFECTED` status)
requires modifications to **19 locations** across 6 specification files.
This section provides the full change map.

### New Semantics

**Before**: `eligible` is calculated only when `status = AFFECTED`.
For all other statuses, `eligible` is either not set or set to `false`.

**After**: `eligible` is always calculated using pure threshold rules:

```
eligible = (
    NOT in_reactive_ltss_phase(product)
    AND resolved_cvss_score >= product.cvss_threshold
)
```

Where:
- `in_reactive_ltss_phase`: `product.end_of_ltss < today <
  product.end_of_reactive_ltss`
- `resolved_cvss_score`: per the CVSS resolution cascade in
  `docs/features/tickets/cvss-scoring.md` (SUSE assessment > highest
  provider > 10.0 fallback)
- `product.cvss_threshold`: from AIMAAS sync (`NULL` = 0, meaning always
  eligible)

### Gate Behavior — No Change

The Resolved gate (C1) does not change. It already scopes its check to
`eligible = true` products under `FIXED` tracks. The fact that
`NOT_AFFECTED` products may now have `eligible = true` does not affect
the gate — those products are under non-`FIXED` tracks and are excluded
by the `FIXED` track condition.

### Files Requiring Modification

#### `docs/features/packages/package-model.md` (9 locations)

| Lines | Section | Change |
|-------|---------|--------|
| 274 | TicketPackageProduct table | Add `DEFAULT true` to `eligible` column (align with `data-model.md`) |
| 335-338 | Axis 2: Eligibility | Rewrite: "Eligibility is evaluated for all products regardless of affectedness status. It represents whether the product meets the CVSS threshold and lifecycle criteria for receiving a fix." Add OQ1 rationale: "The DB default is `true` (conservative toward fix delivery — a missing calculation results in a visible product rather than a silently hidden one, consistent with the CVSS 10.0 fallback principle)." |
| 836-842 | Package Eligibility | Consolidate: replace the restated rule with a cross-reference to Axis 2 section. Do not duplicate the eligibility semantics |
| 569-585 | VA Sets "Affected" on a Track | Move eligibility calculation from this section to a general "Status Propagation" rule that applies to ALL status changes |
| 587-593 | VA Sets Any Other Status | Add eligibility recalculation step (same as the AFFECTED case) |
| 595-601 | VA Overrides a Product Status | Remove the `if AFFECTED` condition on line 598. Eligibility is recalculated for all status values |
| 1655-1658 | Override Product (Reset) | Remove "Recalculation applies only if...AFFECTED; otherwise eligible is set to false". Always recalculate |
| 1511-1512 | Change Track Status endpoint | Remove "(with eligibility evaluation for 'Affected')" parenthetical |
| 61-69 | Design Decision 3 | Update "track stays AFFECTED" to generic wording |

#### `docs/features/packages/package-service.md` (3 locations)

| Lines | Section | Change |
|-------|---------|--------|
| 699-715 | Record Creation Logic | Calculate eligibility for all new products regardless of parent track status. Cross-reference `package-model.md` Axis 2 for the eligibility computation rules (do not restate inline) |
| 149-150 | `set_track_status()` step 6 | Cross-reference target changes (indirect) |
| 225-231 | `set_product_status()` | Add eligibility recalculation step for all status values (guarded by `is_eligible_override = false` — products with eligibility override are not recalculated). Cross-reference `package-model.md` Axis 2 for computation rules |

#### `docs/features/tickets/cvss-scoring.md` (2 locations)

| Lines | Section | Change |
|-------|---------|--------|
| 346 | Recalculation Cascade step 2 | Replace "Products whose status was set directly by a VA are not modified" with "Products with `is_eligible_override = true` are not modified" (OQ2 decision) |
| 347-348 | Recalculation Cascade step 2 | Remove "Products in a final status...are not modified" filter (C14) |

#### `docs/features/packages/product-lifecycle-transitions.md` (2 locations)

| Lines | Section | Change |
|-------|---------|--------|
| 49 | Reactive LTSS detection | Replace `status AFFECTED` filter with intra-dimensional filter: `eligible = true AND is_eligible_override = false` (query products where eligibility is currently true and not manually overridden — these are the candidates that need to be flipped to `eligible = false`). Cross-reference `package-model.md` Axis 2 for eligibility semantics |
| 83-93 | Reactive LTSS sub-task | Same filter change. Remove "eligibility is meaningful only when status is AFFECTED" statement. Cross-reference `package-model.md` Axis 2 |

#### `docs/architecture.md` (1 location)

| Lines | Section | Change |
|-------|---------|--------|
| 229-237 | Package Affectedness Flow | Update step 2 to state eligibility is always calculated, not just for AFFECTED. Update example |

#### `docs/data-model.md` (2 locations)

| Lines | Section | Change |
|-------|---------|--------|
| 172 | ER diagram | Add `DEFAULT true` to `eligible` column definition |
| 548 | TicketPackageProduct table | Change `DEFAULT false` to `DEFAULT true` (OQ1 decision) |

---

## Dimension Definitions

The canonical definition of the three dimensions lives in
`package-model.md` (Design Rationale at lines 12-23 and Three Orthogonal
Dimensions at lines 293-384). As part of this decoupling work, each Axis
section must be enriched with an **essence paragraph** that describes:
(a) what the dimension fundamentally represents, (b) what its inputs
are, and (c) what it does NOT depend on.

### Proposed Essence Paragraphs

**Axis 1: Affectedness** (replace lines 299)

> Property of the source code relative to the CVE. Determined by the VA
> during analysis, or automatically by track release detection.
> Affectedness depends only on whether the source code contains the
> vulnerability — it is independent of CVSS thresholds, product
> lifecycle phase, and delivery pipeline state.

**Axis 2: Eligibility** (replace lines 327-328)

> Property of the product relative to the CVE. Determined purely by CVSS
> score vs. product threshold and product lifecycle phase. Eligibility
> does not logically depend on whether the code is vulnerable — it
> answers "does this product meet the criteria for receiving a fix?"
> regardless of the current affectedness status.

**Axis 3: Delivery** (replace lines 370-372)

> Factual observation of the fix's progress through the SUSE maintenance
> pipeline (SR/incident/RR lifecycle at the track level, `updateinfo.xml`
> advisory detection at the product level). Delivery tracking records
> what happened in IBS — it is independent of whether the code is
> vulnerable (affectedness) or whether the product meets threshold
> criteria (eligibility).

**Design Rationale independence principle** (replace lines 19-23)

> Each dimension is independently computable — its value depends only on
> its own inputs, never on the current state of another dimension.
> Status propagation does not affect eligibility computation, eligibility
> never changes the status label, and delivery tracking is fully
> independent from both. The three dimensions are combined only at
> observation points (the Resolved gate, the anomaly matrix, and
> presentation views) — never during computation or mutation.

### Location

These changes go into `package-model.md` as part of action A1. They are
not cross-cutting (they describe the package tracking model, which is
owned by that spec). Other specs reference the definitions via
cross-references.

---

## Guardrail for Future Cross-Dimensional Coupling

### Proposed Guardrail #24 for `AGENTS.md`

> ### 24. Dimension orthogonality
>
> CRITICAL: The package tracking model defines three orthogonal
> dimensions — Affectedness, Eligibility, and Delivery — as specified
> in `docs/features/packages/package-model.md` (Three Orthogonal
> Dimensions). Each dimension MUST be independently computable: its
> value depends only on its own inputs, never on the current state of
> another dimension.
>
> Before introducing any dependency where one dimension's computation,
> filtering, or mutation depends on the state of another dimension,
> STOP and:
>
> 1. Verify whether the coupling is necessary for business correctness
>    (e.g., the Resolved gate inherently combines all three dimensions)
>    or is an accidental optimization or shortcut
> 2. If necessary, document the justification in the relevant
>    specification with an explicit note: "This is a deliberate
>    cross-dimensional dependency because [reason]"
> 3. If avoidable, restructure the logic to use only the dimension's
>    own inputs
>
> Allowed cross-dimensional combinations:
>
> - **Observation points**: gates, anomaly detection, and presentation
>   views may read multiple dimensions to produce decisions or display
>   — but they must not modify any dimension as a side effect
> - **Post-mutation hooks**: calling `evaluate_ticket_status()` after a
>   mutation is acceptable because the evaluator reads dimensions but
>   does not modify them
>
> Forbidden patterns:
>
> - Filtering dimension A's computation scope by dimension B's state
>   (e.g., "only recalculate eligibility when status is AFFECTED")
> - Skipping dimension A's update because dimension B is in a
>   particular state (e.g., "skip CVSS recalculation for final-status
>   products")
> - Setting dimension A as a side effect of dimension B's mutation
>   (e.g., "set delivery_status = RELEASED when setting status = FIXED")
>
> Note: **intra-dimensional scope optimizations** — where dimension A's
> computation is skipped for records where the result is provably
> inconsequential within the same dimension (e.g., skipping release
> detection for tracks already in `FIXED` status) — are not
> cross-dimensional couplings and do not trigger this guardrail.

### Reviewer Extension

Rather than creating a dedicated reviewer, extend `@spec-coherence-reviewer`
with an additional check: "Verify that no specification introduces a
dependency where one dimension's computation or scope filter uses the
state of another dimension without explicit justification referencing the
allowed cross-dimensional combinations in Guardrail 24." This keeps the
review surface consolidated — the spec-coherence-reviewer already looks
for contradictions between specs, and dimension coupling is a specific
category of contradiction against the orthogonality principle.

---

## Action Summary

### Phase 1: Specification Changes

All changes are to specification documents only (no implementation code
exists yet).

| ID | Action | Files | Complexity |
|----|--------|-------|------------|
| **A1** | Decouple eligibility from AFFECTED (C2, C3, C4, C14) + add dimension essence paragraphs + update independence principle + OQ1/OQ2 changes | `package-model.md`, `package-service.md`, `cvss-scoring.md`, `product-lifecycle-transitions.md`, `architecture.md`, `data-model.md` | High (19 locations, semantic change) |
| **A2** | Remove `delivery_status` from release detection scope (C5) | `ibs-track-release-detection.md` | Low (2 locations, line removal) |
| **A3** | Remove affectedness filter from SR processing (C6) | `ibs-submission-tracking.md` | Medium (2 locations + consistency review) |
| **A4** | Redesign RabbitMQ consumer codestream set (C7) | `ibs-rabbitmq-integration.md` | Medium (single set with metadata, scope change) |
| **A5** | Update submission chain relevance (C11) | `ibs-submission-tracking.md` | Low (wording change) |
| **A6** | Separate git release detection mutations (C13) | `git-track-release-detection.md` | Low (restructure to two calls) |
| **A7** | Remove `evaluate_ticket_status()` from `set_track_delivery_status()` (C15) + add spec note: "does not trigger ticket status re-evaluation because `delivery_status` is not part of any gate condition" | `package-service.md` | Low (remove one step + add documentation note) |
| **A8** | Add Guardrail #24 (dimension orthogonality) | `AGENTS.md` | Low (new guardrail section) |
| **A9** | Extend `@spec-coherence-reviewer` with dimension coupling check | `.opencode/agents/spec-coherence-reviewer.md` | Low (add check to existing agent) |

### Phase 2: Reviewers (post-implementation)

After all specification changes are applied, invoke the following
reviewers. Order matters — run coherence and gap analysis first, then
domain-specific reviewers.

| Reviewer | Reason | Scope |
|----------|--------|-------|
| `@spec-coherence-reviewer` | Multiple specs modified simultaneously; verify no contradictions introduced | Run once per modified spec (6 invocations): `package-model.md`, `package-service.md`, `cvss-scoring.md`, `product-lifecycle-transitions.md`, `ibs-track-release-detection.md`, `ibs-submission-tracking.md` |
| `@spec-gap-analyzer` | Verify decoupled eligibility semantics are complete; check for missing edge cases | Run on: `package-model.md`, `cvss-scoring.md`, `product-lifecycle-transitions.md` |
| `@design-reviewer` | Architectural review of the "pure threshold" eligibility semantics | Run on: `package-model.md` (primary spec) |
| `@data-model-reviewer` | Verify `eligible` column semantics are consistent with new behavior | Run on: `data-model.md` (if modified) |
| `@docs-reviewer` | Verify documentation completeness after multi-file changes | Run on: `architecture.md` |
| `@docs-placement-reviewer` | Verify that eligibility rules are not misplaced after redistribution | Run on: `package-model.md`, `cvss-scoring.md` |
| `@api-convention-reviewer` | Verify API endpoint descriptions are consistent with new eligibility semantics | Run on: `package-model.md` (endpoint definitions) |
| `@ticket-integrity-reviewer` | Verify audit events are correct for new eligibility behavior | Run on: `package-service.md` |

---

## Anomaly Observer

### Current State

Anomalous combinations of affectedness and delivery (e.g.,
`AFFECTED + RELEASED`, `NOT_AFFECTED + IN_PROGRESS`) are currently
defined as a static matrix in `package-model.md:523-558`. The spec notes
these are "destined to be integrated into the future Review Queue" but
no implementation is defined.

### Proposed Design

Implement anomaly detection as an **independent observer service** that
reads the three dimensions without modifying them:

```
AnomalyObserver:
  - Input: (affectedness_status, eligible, delivery_status, released_at)
  - Output: list of anomaly tags (e.g., "affected_but_released",
    "not_affected_but_in_progress")
  - Trigger: called after any dimension mutation (via post-mutation hook
    in package_service, similar to evaluate_ticket_status)
  - Effect: writes anomaly tags to a separate table or flag, consumed
    by the Review Queue UI
  - Constraint: NEVER modifies affectedness, eligibility, or delivery
```

This keeps the anomaly logic decoupled from the dimension management
code. The observer is a pure function of the three dimensions' current
values, with no side effects on those dimensions.

### Anomaly Matrix (extended with eligibility)

With eligibility decoupled, the anomaly matrix can be extended to include
eligibility-related anomalies:

| Affectedness | Eligible | Delivery | Anomaly? | Signal |
|-------------|----------|----------|----------|--------|
| AFFECTED | false | RELEASED | Yes | Fix released for ineligible product |
| AFFECTED | true | RELEASED | Yes | Fix released but track not FIXED |
| NOT_AFFECTED | true | IN_PROGRESS | Yes | SR in progress for unaffected code |
| NOT_AFFECTED | true | RELEASED | Yes | Fix released for unaffected code |
| WONT_FIX | true | IN_PROGRESS | Yes | SR in progress despite won't-fix |
| WONT_FIX | true | RELEASED | Yes | Fix released despite won't-fix |
| ANALYSIS | true | RELEASED | Yes | Fix released before analysis complete |

The observer evaluates all three dimensions simultaneously but only
produces observational output — no mutations.

### Infrastructure Prerequisite: Monitoring Final-Status Tracks

If the anomaly observer needs to detect fixes present in codestreams
where the track is in a final affectedness status (`NOT_AFFECTED`,
`WONT_FIX`, or already `FIXED`), the current release detection
infrastructure would need to be extended:

- **`IBSEventConsumer`**: the `package.commit` event filter currently
  builds the monitored codestream set from tracks with `status in
  (ANALYSIS, AFFECTED)`. To detect fixes for final-status tracks, the
  set would need to include all tracked codestreams regardless of
  affectedness status. The consumer would still NOT transition
  final-status tracks to `FIXED` (the automatic transition table
  prohibits this), but it would report the detection to the anomaly
  observer.
- **`check_ibs_track_releases` fetcher**: same scope change needed. The
  periodic catch-up scan currently filters by `status in (ANALYSIS,
  AFFECTED)`. Extending to all statuses would increase the scan scope
  but provide complete anomaly coverage.

This is a decision to be made when the anomaly observer / Review Queue
feature is designed. It is NOT part of the current decoupling work.
See open point #4 in `docs/drafts/open-points.md`.

---

## Open Questions (Resolved)

1. **DB default for `eligible`**: Currently `DEFAULT false`. With
   eligibility always calculated at creation time, the default is never
   visible to API consumers under normal operation. However, in the event
   of a bug where the calculation is skipped, the default acts as a
   safety net.

   **Decision**: change to `DEFAULT true`. Rationale: a false positive
   (product appears eligible when it should not → VA notices and corrects
   manually) is preferable to a false negative (product hidden from the
   VA when it should receive a fix → silent omission). This is consistent
   with the CVSS 10.0 fallback principle: in absence of data, assume the
   conservative-toward-fix-delivery case.

   **Failure mode**: if eligibility calculation is skipped due to a bug,
   falsely-eligible products block ticket resolution (the Resolved gate
   requires `released_at IS NOT NULL` for all `eligible = true` products
   under FIXED tracks). This is the intended safety net — blocked
   resolution is visible and correctable; silent omission of eligible
   products is not.

   **Impact**: modify `docs/data-model.md` (line 548 + ER diagram at
   line 172) as part of A1.

2. **`is_status_override` interaction with eligibility recalculation**:
   The CVSS cascade (`cvss-scoring.md:346`) currently says "Products
   whose status was set directly by a VA are not modified" — this
   blanket exemption incorrectly blocks eligibility recalculation based
   on `is_status_override`, conflating two independent mechanisms.

   **Decision**: remove the `is_status_override` exemption from
   eligibility recalculation. Only `is_eligible_override` controls
   eligibility exemption. Status override and eligibility override are
   independent mechanisms — this is already the case in
   `package-model.md:574-580` (status propagation) where the two flags
   are treated independently. The `cvss-scoring.md` line is the outlier.

   **Consequence**: products with `is_status_override = true` (e.g., a VA
   overrode status to `WONT_FIX`) still participate in eligibility
   recalculation during CVSS cascades. The status override controls only
   the affectedness dimension; the eligibility override controls only the
   eligibility dimension. A product in `WONT_FIX + eligible = true` is
   operationally inconsequential (the Resolved gate only checks products
   under `FIXED` tracks).

   **Impact**: modify `cvss-scoring.md:346` as part of A1 (C14 action).
   Replace "Products whose status was set directly by a VA are not
   modified" with "Products with `is_eligible_override = true` are not
   modified".

3. **Performance of universal eligibility recalculation**: With C2
   removed, every CVSS score change recalculates eligibility for ALL
   products linked to a ticket (not just AFFECTED ones).

   **Decision**: confirmed — no performance concern. Rationale: (a) most
   tickets have fewer than 50 products, (b) CVSS score resolution
   happens once per ticket, (c) per-product eligibility check is two
   O(1) operations (date comparison + numeric comparison), (d) the batch
   case (default version change) is already bottlenecked by ticket count,
   not products-per-ticket. No optimization needed.

---

## Session Continuity Notes

### What Has Been Done

- [x] Complete inventory of all 15 coupling points
- [x] Classification of each coupling (KEEP: 6, REMOVE: 7, SIMPLIFY: 3)
- [x] C2 impact analysis: 19 specification locations identified
- [x] Decision: eligibility uses "pure threshold" semantics
- [x] Decision: anomaly observer as independent service
- [x] Reviewer plan defined
- [x] C15 revised from KEEP to REMOVE (bug-masking risk)
- [x] C7 clarified: final-status tracks are immune to automatic
      transitions; monitoring them for release detection is wasted work;
      anomaly observer may need them in the future
- [x] Dimension essence paragraphs drafted for package-model.md
- [x] Guardrail #24 (dimension orthogonality) drafted for AGENTS.md
- [x] Reviewer extension for @spec-coherence-reviewer defined
- [x] Open point #4 added to docs/drafts/open-points.md (anomaly
      observer replacing static matrix)
- [x] Open questions 1-3 resolved
- [x] Ran design reviewer, spec-gap-analyzer, docs-placement-reviewer
- [x] C7 redesigned: single set with metadata replaces dual-set approach
- [x] Reactive LTSS filter changed to intra-dimensional
      (`eligible = true AND is_eligible_override = false`)
- [x] C6 scope bounded to active tickets
- [x] Guardrail #24 refined with scope optimization exception
- [x] Change map updated with reviewer feedback (OQ1 rationale
      placement, consolidation note, is_eligible_override guard, A7
      documentation note, package-model.md:274 DEFAULT)
- [x] Pre-execution reviewer pass: design-reviewer, spec-coherence,
      spec-gap-analyzer, docs-placement — all passed (minor issues only)
- [x] Integrated reviewer findings: OQ1 failure mode note, C6
      performance note, OQ2 consequence note, C13 atomicity note,
      cross-reference consolidation notes, fixed location count (15→19)

### What Remains

- [ ] Execute A1: modify `package-model.md` (9 locations, including
      essence paragraphs, independence principle, and OQ1 rationale)
- [ ] Execute A1: modify `package-service.md` (3 locations)
- [ ] Execute A1: modify `cvss-scoring.md` (2 locations)
- [ ] Execute A1: modify `product-lifecycle-transitions.md` (2 locations)
- [ ] Execute A1: modify `architecture.md` (1 location)
- [ ] Execute A1: modify `data-model.md` (2 locations)
- [ ] Execute A2: modify `ibs-track-release-detection.md` (2 locations)
- [ ] Execute A3: modify `ibs-submission-tracking.md` (2 locations)
- [ ] Execute A4: modify `ibs-rabbitmq-integration.md` (single set with
      metadata design)
- [ ] Execute A5: modify `ibs-submission-tracking.md` (chain relevance)
- [ ] Execute A6: modify `git-track-release-detection.md` (two calls)
- [ ] Execute A7: modify `package-service.md` (remove evaluate call
      from set_track_delivery_status + add documentation note)
- [ ] Execute A8: add Guardrail #24 to `AGENTS.md`
- [ ] Execute A9: extend `@spec-coherence-reviewer` with dimension
      coupling check
- [x] Resolve open questions 1-3
  - OQ1: `DEFAULT true` (conservative toward fix delivery)
  - OQ2: remove `is_status_override` exemption from eligibility cascade
  - OQ3: no performance concern, no action needed
- [ ] Run all Phase 2 reviewers
- [ ] Add open point #4 to `docs/drafts/open-points.md` — **DONE**
- [ ] Move draft to `docs/drafts/dimension-decoupling.md` — **DONE**
- [ ] Delete plan file — **DONE**
