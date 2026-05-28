# Review: cvss-scoring

**Spec**: `docs/features/tickets/cvss-scoring.md`
**Last reviewed**: 2026-05-28
**Reviewers**: Coherence (partial, during CVE resource path migration)

---

## Coherence

### CVS-COH-01 — Eligibility Threshold section contradicts Eligibility Score Resolution (Medium)

**Category**: Intra-spec contradiction
**Status**: OPEN

The "Eligibility Threshold" section (lines 279-293) describes a 3-step
cascade for resolving the CVSS score used in product eligibility
comparisons:

> 1. SUSE assessment of the default CVSS version
> 2. Highest score among all providers for the default version
> 3. No score available -> treat as 10.0

However, the authoritative "Eligibility Score Resolution" section
(lines 69-91) in the same spec explicitly states:

> "This resolution uses **only** the SUSE assessment of the configured
> default CVSS version. No fallback to other providers or other versions
> is applied"

The two sections describe different algorithms. Step 2 in the Eligibility
Threshold section (fallback to highest provider score) directly
contradicts the Eligibility Score Resolution section (SUSE-only, no
provider fallback).

`docs/features/packages/package-model.md` (Axis 2: Eligibility) agrees
with the Eligibility Score Resolution section (SUSE-only), confirming
that the Eligibility Threshold section is the stale description.

**Recommendation**: remove the 3-step cascade from the Eligibility
Threshold section and replace it with a cross-reference to the
Eligibility Score Resolution section, or rewrite it to match (SUSE
assessment only, 10.0 fallback when absent).
