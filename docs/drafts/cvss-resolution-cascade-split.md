# CVSS Resolution Cascade Split: Severity vs. Eligibility

**Origin**: review finding CVES-COH-01 (cve-service review, COH section) and
CVS-COH-01 (cvss-scoring review, COH section) — both flag an internal
contradiction in `docs/features/tickets/cvss-scoring.md` between the
"Eligibility Score Resolution" section (2-step SUSE-only) and the "Eligibility
Threshold" section (3-step with provider fallback).

**Decision**: adopt the 2-step SUSE-only cascade as the canonical behavior for
eligibility. See design decisions below for rationale.

---

## Background

`docs/features/tickets/cvss-scoring.md` declares (line ~34):

> "Sentinel uses **two distinct resolution strategies** depending on the
> consumer."

However, only one function currently exists in `services/cvss.py`:
`resolve_cvss_score`, described as "Implements the 3-step resolution cascade."
This single function is called for both severity and eligibility, causing two
problems:

1. **Internal contradiction**: "Eligibility Score Resolution" (lines ~69-91)
   says SUSE-only with 10.0 fallback; "Eligibility Threshold" (lines ~278-292)
   says 3-step with all-providers fallback. They are incompatible.

2. **Wrong step count**: the severity cascade documented in the spec (lines
   ~37-60) has 5 steps (SUSE default → SUSE other version → highest provider
   default → highest provider other → absent), not 3. The "3-step" label
   matches neither cascade.

---

## Design Decisions

### Decision 1: Eligibility uses 2-step SUSE-only cascade

The "Eligibility Score Resolution" section (lines ~69-91) is the canonical
definition. Rationale from the spec itself: "Only the authoritative internal
assessment should determine this." The 2-step cascade is:

1. SUSE assessment of the default CVSS version → if present, use this score
2. Not resolvable → treat as 10.0 (worst-case; product is always eligible)

**Rejected alternative** (3-step with providers): would allow external scores
(NVD, Red Hat, CNA) to determine eligibility in the absence of a SUSE
assessment. This introduces dependency on uncontrolled external data for an
internal policy decision. `package-model.md` (lines ~360-367) and `AGENTS.md`
(Guardrail 13, after this fix) explicitly reject this.

### Decision 2: Two separate functions, not one function with a parameter

The two cascades diverge in semantics, not just logic:

| | `resolve_severity_score` | `resolve_eligibility_score` |
|---|---|---|
| Return type | `(score, provider) or None` | `Decimal` (always a value) |
| Fallback | `None` (no score available) | `10.0` (conservative policy) |
| Provider scope | All providers | SUSE only |
| Version scope | Default + other versions | Default version only |
| Step count | 5 | 2 |

A `suse_only: bool` parameter would change both the cascade logic AND the
return type semantics, producing a function that is hard to reason about and
creates a silent failure mode (forgetting the parameter → wrong cascade used
for eligibility without any error).

Two distinct strategies → two distinct functions, as the spec itself states.

### Decision 3: Rename `resolve_cvss_score` → `resolve_severity_score`

The current name is misleading (it resolves a score for severity, not a
generic CVSS score). Renaming to `resolve_severity_score` makes the intent
explicit and symmetric with `resolve_eligibility_score`. This also forces
correction of the wrong "3-step" description to "5-step severity resolution
cascade."

### Decision 4: `calculate_severity` is unchanged

This function takes an already-resolved numeric score and maps it to a
severity label (rating scale: 0.0→None, 0.1-3.9→Low, 4.0-6.9→Medium,
7.0-8.9→High, 9.0-10.0→Critical). It is the second step of the severity
pipeline, not a resolver. It is not involved in eligibility calculation.
No changes needed.

---

## Final Function Table for `services/cvss.py`

| Function | Inputs | Output | Role |
|---|---|---|---|
| `resolve_severity_score` | CVE assessments, default CVSS version | `(score, provider)` or `None` | Severity cascade: 5-step (SUSE default → SUSE other version → highest provider default → highest provider other → absent) |
| `resolve_eligibility_score` | CVE assessments, default CVSS version | `Decimal` (score) | Eligibility cascade: 2-step (SUSE default → 10.0 fallback). Always returns a value |
| `calculate_severity` | CVSS score (float) | Severity enum | Maps numeric score to label using rating scale. Unchanged |
| `validate_cvss_vector` | Vector string | Parsed metrics + version + calculated score | Parses vector, detects version from prefix, validates format, and computes base score. Unchanged |

---

## Callers After the Fix

| Caller | Uses `resolve_severity_score` | Uses `resolve_eligibility_score` |
|---|---|---|
| API read path (`GET .../cvss`) | Yes — `resolved_score`, `resolved_provider`, `resolved_severity` | No |
| `ticket_mutations` (3 CVSS fns) | Yes — step 4 (new resolved score for severity) | Yes — step 7a (eligibility re-evaluation) |
| `package_service` — record creation | No | Yes — initial eligibility at `TicketPackageProduct` creation |
| `package_service` — override reset | No | Yes — recalculate with standard rules when `eligible=null` |
| Batch recalculation (default version change) | Via `ticket_mutations` | Via `ticket_mutations` |

---

## Detailed Change Plan

### Step 1 — `docs/features/tickets/cvss-scoring.md` (HIGH priority)

This is the primary spec and requires the most changes.

**1a. Service Architecture function table** (around line 534):
- Rename `resolve_cvss_score` → `resolve_severity_score`
- Update description: "Implements the 3-step resolution cascade" →
  "Implements the severity resolution cascade (5-step: SUSE default →
  SUSE other version → highest provider default → highest provider other
  → absent)"
- Add new row for `resolve_eligibility_score`:
  - Inputs: `CVE assessments, default CVSS version`
  - Output: `Decimal (score)` — always returns a value
  - Description: "Implements the eligibility score resolution (2-step,
    SUSE-only: SUSE default version → 10.0 fallback)"

**1b. "Eligibility Threshold" section** (around lines 278-292):
- Remove step 2 ("Highest score among all providers for the default version")
- Align to 2-step: (1) SUSE assessment of default version → use this;
  (2) Not resolvable → treat as 10.0
- Add explicit cross-reference to "Eligibility Score Resolution" section
  as the canonical definition
- Remove the phrase contradicting step 2 is now consistent with
  "Eligibility Score Resolution"

**1c. Recalculation Cascade section** (around lines 366-401):
- Clarify that step 1 of the cascade calls `resolve_severity_score` for
  the severity result
- Clarify that step 2 (eligibility re-evaluation) calls
  `resolve_eligibility_score` — a separate call with different semantics
- The two calls are independent: severity may be `None` while eligibility
  always produces a score (10.0 fallback)

**1d. Write path description / ticket_mutations flow** (around lines 559-564):
- Step 4: "Call `cvss.resolve_severity_score()` to determine the new
  resolved score (for severity)"
- Add step 4b: "Call `cvss.resolve_eligibility_score()` to determine the
  eligibility score (SUSE-only, 10.0 fallback)"
- Step 5: "Call `cvss.calculate_severity()` to derive the new severity
  from the severity score"
- Step 7a: "Re-evaluate product eligibility using the eligibility score
  from step 4b" (instead of "using the new score")

**1e. "Eligibility Score Resolution" section** (around lines 69-91):
- Already correct. Add a note: "This cascade is implemented by
  `resolve_eligibility_score` in `services/cvss.py`."

**1f. Severity Resolution Cascade section** (around lines 37-60):
- Already describes 5 steps correctly. Add a note: "This cascade is
  implemented by `resolve_severity_score` in `services/cvss.py`."

**1g. `resolved_*` API response description** (around line 453):
- Update the sentence "The `resolved_*` fields reflect the result of the
  resolution cascade for the current default version (which score and
  provider Sentinel is using for decisions)."
- Replace with: "The `resolved_*` fields reflect the result of the
  **severity** resolution cascade for the current default version —
  identifying which score and provider Sentinel uses for severity
  derivation and display. These fields do NOT reflect the eligibility
  resolution (which is SUSE-only; see Eligibility Score Resolution)."

### Step 2 — `AGENTS.md` (HIGH priority)

**Guardrail 13** (around lines 445-462):

The current guardrail describes a single 3-step cascade that is wrong for
both severity (which has 5 steps, including cross-version fallback) and
eligibility (which is SUSE-only, with no provider fallback). Adding a note
to the existing text is insufficient — the guardrail must be rewritten with
two clearly separated sub-sections.

**Full rewrite required**:

- Remove the existing 3-step cascade bullet list entirely
- Replace with two named sub-sections:

  **For severity resolution** (used for: display, notifications, triage):
  5-step cascade — (1) SUSE assessment of default version; (2) SUSE
  assessment of other version (prefer highest version number if multiple);
  (3) highest score among all providers for the default version; (4)
  highest score among all providers for any other version; (5) absent
  (no score available). Cross-reference `cvss-scoring.md` "Severity
  Resolution Cascade" as the authoritative definition.

  **For eligibility resolution** (used for: product eligibility threshold
  comparison): 2-step SUSE-only cascade — (1) SUSE assessment of default
  version → use this score; (2) not resolvable → treat as 10.0
  (worst-case conservative fallback). No fallback to other providers or
  other versions. Cross-reference `cvss-scoring.md` "Eligibility Score
  Resolution" as the authoritative definition.

- The invariant principles to preserve (keep verbatim or equivalent):
  - Every component must resolve the CVSS version from the system-wide
    configuration (`default_cvss_version` setting) — never hardcode
    `"3.1"` or `"4.0"`
  - Two distinct resolution strategies exist — the caller MUST use the
    correct one for its consumer context (severity vs. eligibility)

### Step 3 — `docs/features/packages/package-service.md` (MEDIUM priority)

Two sections describe eligibility recalculation generically without stating
SUSE-only:

**3a.** Lines ~337: "Recalculate eligibility using standard rules (CVSS score
resolution per configured version → compare against product threshold from
AIMAAS)"
- Update to: "Recalculate eligibility using `cvss.resolve_eligibility_score()`
  (SUSE-only, 2-step cascade — see Eligibility Score Resolution in
  `docs/features/tickets/cvss-scoring.md`) → compare against product threshold"

**3b.** Lines ~344: "Eligibility recalculation uses the same resolution logic
as `re_evaluate_product_eligibility` (CVSS score resolution per
`default_cvss_version`)"
- Add: "specifically `resolve_eligibility_score` (SUSE assessment of default
  version only; fallback to 10.0 if no SUSE assessment exists)"

**3c.** Relationship table (line ~74): update reference from
`resolve_cvss_score` to `resolve_eligibility_score` for eligibility context.

**3d.** `docs/features/tickets/ticket-mutations.md` — the three CVSS mutation
functions (`create_cvss_assessment`, `update_cvss_assessment`,
`delete_cvss_assessment`) each list a recalculation step as "Recalculate
ticket severity via `cvss.py` resolution cascade" (around lines ~377, ~426,
~464) without mentioning eligibility re-evaluation. Update each to explicitly
name both calls:
- "Recalculate ticket severity via `cvss.resolve_severity_score()` (5-step
  severity cascade)"
- "Re-evaluate product eligibility via `cvss.resolve_eligibility_score()`
  (2-step SUSE-only cascade, separate call — the eligibility score may differ
  from the severity score when SUSE has not assessed the default version)"

Without this update, an implementer reading only `ticket-mutations.md` may
call only `resolve_severity_score()` and omit the separate
`resolve_eligibility_score()` call, silently producing wrong eligibility
decisions.

### Step 4 — `docs/features/platform/system-settings.md` (MEDIUM priority)

**Line ~36**: "Recalculate severity... Re-evaluate product eligibility...
using the new default version's resolution cascade"

Update "resolution cascade" to distinguish:
- Severity: recalculated using `resolve_severity_score` (5-step,
  multi-provider)
- Eligibility: re-evaluated using `resolve_eligibility_score` (2-step,
  SUSE-only)

### Step 5 — `docs/features/tickets/cve-tracking.md` (MEDIUM priority)

**Lines ~215-217**: Critical CVE notification uses "resolved CVSS score >= 9.0"

Clarify that this check uses `resolve_severity_score` (the multi-provider
severity cascade), not `resolve_eligibility_score`. The notification is
triggered by the severity score, which may include external provider data.

Also check: line ~231-232 "severity field on CVE is a denormalized field
derived from the CVSS resolution cascade" — update "resolution cascade" to
"severity resolution cascade" for precision.

### Step 6 — Review file cleanups

**6a.** `docs/reviews/cve-service.md`: mark CVES-COH-01 as RESOLVED with
compact format:
```
**Status**: RESOLVED — Eligibility cascade aligned to 2-step SUSE-only;
resolve_cvss_score renamed to resolve_severity_score; resolve_eligibility_score
added as dedicated function (2026-06-03)
```

**6b.** `docs/reviews/cvss-scoring.md`: mark CVS-COH-01 as RESOLVED with
the same resolution text.

**6c.** Update `docs/reviews/.tracking.json`:
- `cve-service`: decrement `COH.M` by 1, increment `resolved` by 1
- `cvss-scoring` (if enabled): decrement `COH` finding, increment `resolved`

**6d.** Update `docs/reviews/README.md` to reflect new counts.

### Step 7 — Run post-fix reviewers

After all spec changes are applied, run these reviewers in parallel on the
affected specs:

| Reviewer | Specs to run on | Trigger |
|---|---|---|
| `spec-coherence-reviewer` | `cvss-scoring`, `cve-service`, `package-service`, `ticket-mutations` | Terminology changed, cross-spec references modified |
| `spec-gap-analyzer` | `cvss-scoring` | New function defined, business rules changed |
| `docs-placement-reviewer` | `cvss-scoring`, `AGENTS.md` | New rules/patterns added, Guardrail modified |

Run each reviewer as a Task agent per the single-reviewer mechanism in
`.opencode/commands/review-spec/review-procedure.md`.

### Step 8 — Delete this draft file

Once the changes are applied and verified (Step 7), delete
`docs/drafts/cvss-resolution-cascade-split.md`.

---

## Open Questions

*None at this time. All design decisions have been resolved.*

---

## Files Verified as Already Aligned (No Changes Needed)

- `docs/features/packages/package-model.md` — already describes 2-step
  SUSE-only (lines ~360-367, ~1591-1599)
- `docs/features/tickets/cvss-scoring.md` — "Eligibility Score Resolution"
  section (lines ~69-91) already correct; it is the canonical definition
- `docs/features/tickets/ticket-service.md` — no direct CVSS resolution calls
- `docs/features/tickets/tickets.md` — consumes `eligible` flag only
- `docs/features/packages/product-lifecycle-transitions.md` — triggers
  eligibility re-evaluation as a sub-operation via
  `package_service.set_product_eligibility()`. After the fix, that function
  will call `resolve_eligibility_score()`. The spec itself contains no cascade
  logic — it delegates entirely to `package-model.md` and `cvss-scoring.md`
- `docs/features/packages/product-catalog.md` — defines threshold data, not
  cascade logic
- `docs/data-model.md` — schema definitions only
- `docs/architecture.md`, `docs/configuration.md`, `docs/api-spec.md`,
  `docs/system-map.md` — generic references, not affected
