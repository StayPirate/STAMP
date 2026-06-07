# Review: cvss-scoring

**Spec**: `docs/features/tickets/cvss-scoring.md`
**Last reviewed**: 2026-06-07
**Reviewers**: Coherence, Gap Analysis

---

## Gap Analysis

### GAP-CVS-003 — No explicit specification for batch recalculation when SUSE has old-default-version assessment only (High)

**Status**: RESOLVED — Auto-resolved: the spec already covers this scenario — "SUSE has not scored the default version" is listed as an explicit condition in Eligibility Score Resolution (line 90), and the gate requiring both v3.1 and v4.0 for Analysis→Analyzed limits the impact to in-progress Analysis tickets, which is expected behavior (2026-06-07)

### GAP-CVS-008 — `sync_cvss_redhat` fetcher error handling explicitly marked TBD (High)

**Category**: Unspecified error paths
**Status**: OPEN

`sync_cvss_redhat` fetcher error handling is explicitly marked "TBD" in the spec. Multiple failure modes are unspecified: HTTP 404 (CVE not in Red Hat's database — should existing assessment be deleted or preserved?), HTTP 429, HTTP 5xx, network timeouts, and malformed responses.

### GAP-CVS-001 — `resolve_eligibility_score` input contract does not specify pre-filtering of assessments (Medium)

**Category**: Boundary conditions
**Status**: OPEN

`resolve_eligibility_score` input contract does not specify whether it receives all CVE assessments (from which it internally filters for SUSE) or only pre-filtered SUSE assessments. This matters when SUSE has assessed a non-default version — the behavior differs based on the caller's filtering choice.

### GAP-CVS-002 — Batch recalculation for default version change has no named entry point in `ticket_mutations` (Medium)

**Category**: Unspecified error paths
**Status**: OPEN

Batch recalculation task for default version change has no named entry point in `ticket_mutations`. The spec says to call "the same `ticket_mutations` functions", but none of the three documented functions (create/update/delete) match the scenario (no assessment is being changed). A dedicated recalculation entry point must be defined or named.

### GAP-CVS-004 — Step numbering 4 and 4b creates ordering ambiguity for `CVE.severity` update (Medium)

**Category**: Boundary conditions
**Status**: OPEN

Step numbering 4 and 4b creates ordering ambiguity for `CVE.severity` update. Step 6 ("Update CVE.severity") follows steps 4 and 4b, but the spec does not explicitly state that `CVE.severity` uses only the result of step 4 (`resolve_severity_score`) and never step 4b (`resolve_eligibility_score`). In divergence scenarios (SUSE only has non-default version), the two calls return different values.

### GAP-CVS-005 — Recalculation Cascade note on Resolved tickets inconsistent with `ensure_ticket_operable()` semantics (Medium)

**Category**: Missing state transitions
**Status**: OPEN

The Recalculation Cascade note about Resolved tickets is inconsistent with `ensure_ticket_operable()` semantics. The note says re-evaluation on Resolved tickets "can only occur" via VA manual action — but `ensure_ticket_operable()` only blocks Ignored/Duplicated. The spec does not specify what `reconcile_ticket_status()` does when a CVSS change makes a Resolved ticket's eligibility change (could open or close resolution gates).

### GAP-CVS-006 — Concurrency gap in batch recalculation for concurrent default version changes (Medium)

**Category**: Temporal/concurrency scenarios
**Status**: OPEN

Concurrency gap: two concurrent default version changes can cause the batch recalculation task to process some tickets with the old version and others with the new version. The spec does not specify whether the batch reads the default version once at startup or per-ticket, nor what happens if a second version change is triggered mid-execution.

### GAP-CVS-007 — `GET /cves/{cve_id}/cvss` exposes severity cascade resolved fields but no eligibility score (Low)

**Category**: Boundary conditions
**Status**: OPEN

The `GET /cves/{cve_id}/cvss` API response exposes `resolved_*` fields for the severity cascade but provides no mechanism to observe the eligibility score. A VA cannot directly verify via the API why a product's `eligible` flag changed — they must infer it from checking whether a SUSE assessment for the default version exists.

### GAP-CVS-009 — Batch recalculation scope for soft-deleted products not explicitly confirmed (Low)

**Category**: Data lifecycle gaps
**Status**: OPEN

Batch recalculation scope for soft-deleted products is not explicitly confirmed. `package-model.md` Design Decision 8 states soft-deleted records continue to receive eligibility updates, but the batch spec and write-path spec do not state this explicitly.

---

## Coherence

### CVS-COH-06 — Recalculation Cascade step 2 implies direct eligibility writes; must route through `package_service` (High)

**Category**: Incompatible flows
**Status**: OPEN

Recalculation Cascade step 2 implies direct updates to `TicketPackageProduct.eligible`, but Guardrail 16 and `package-service.md` require all eligibility mutations to go through `package_service.set_product_eligibility()`. Without this routing, eligibility changes during CVSS recalculation would bypass the audit trail (`product_eligibility_changed`) and `reconcile_ticket_status()` call that `package_service` owns for eligibility mutations.

### CVS-COH-04 — Recalculation Cascade audit trail omits `cvss_assessment_changed` event (Medium)

**Category**: Contradictory definitions
**Status**: OPEN

Recalculation Cascade §Audit trail (step 4) lists only `severity_changed` and `product_eligibility_changed` events, but omits `cvss_assessment_changed` — which `ticket-mutations.md` confirms is always emitted as the primary event by all three CVSS mutation functions.

### CVS-COH-07 — Recalculation Cascade step 3 note overstates restriction as architectural invariant (Medium)

**Category**: Incompatible flows
**Status**: OPEN

The note says eligibility recalculation on Resolved tickets "can only occur" via VA manual SUSE CVSS changes — but `ensure_ticket_operable()` only blocks Ignored/Duplicated, not Resolved tickets. Any caller of `ticket_mutations` (including future fetchers) could trigger the cascade on Resolved tickets.

### CVS-COH-01 — Eligibility Threshold section contradicts Eligibility Score Resolution (Medium)

**Status**: RESOLVED — Eligibility cascade aligned to 2-step SUSE-only; resolve_cvss_score renamed to resolve_severity_score; resolve_eligibility_score added as dedicated function (2026-06-03)

### CVS-COH-02 — Write-path flow uses non-standard step label `4b` instead of renumbered sequence (Low)

**Category**: Contradictory definitions
**Status**: OPEN

Write-path flow in `cvss-scoring.md` uses non-standard step label `4b` instead of renumbered sequence. The flow uses steps 1,2,3,4,4b,5,6,7,8 — syntactically irregular. Creates ambiguity about whether step 5 depends on step 4 alone or both 4 and 4b. The per-function descriptions in `ticket-mutations.md` don't use this "4b" label.

### CVS-COH-03 — `auto_assign_actor()` call absent from CVSS write-path summary (Low)

**Category**: Contradictory definitions
**Status**: OPEN

`auto_assign_actor()` call is absent from the CVSS write-path summary in `cvss-scoring.md`. The write-path summary (Service Architecture → `services/ticket_mutations.py`) omits `auto_assign_actor()` between `ensure_ticket_operable()` and "Persist the CVECVSSAssessment record change", while `ticket-mutations.md` §Gate-Relevant Mutation Operations defines this as a standard step in all mutation functions.

### CVS-COH-05 — §When Severity is Recalculated lists a redundant trigger (Low)

**Category**: Contradictory definitions
**Status**: OPEN

§When Severity is Recalculated lists a redundant trigger. Trigger 3 ("The SUSE assessment is added or modified by a VA") is a strict subset of Trigger 1 ("A CVSS assessment is added, modified, or removed"). Only two independent triggers exist, not three.

### CVS-COH-08 — Term "cascade" overloaded across Severity Resolution Cascade and Recalculation Cascade (Low)

**Category**: Terminology issues
**Status**: OPEN

"cascade" used for both score resolution strategy (Severity Resolution Cascade) and the recalculation side-effect chain (Recalculation Cascade). The "Eligibility Score Resolution" section correctly avoids the term "cascade" in its title, but the naming is inconsistent across sections and propagates into referencing specs.

### CVS-COH-09 — `product-lifecycle-transitions.md` implies async path where `cvss-scoring.md` describes synchronous path (Low)

**Category**: Terminology issues
**Status**: OPEN

`product-lifecycle-transitions.md` references `cvss_change` reason for `re_evaluate_product_eligibility` sub-task, implying an enqueued async path. But `cvss-scoring.md` describes CVSS-triggered eligibility updates as synchronous (inline in the CVSS mutation transaction). These describe different mechanisms for the same trigger.

### CVS-COH-10 — §Cross-references omits `ticket-mutations.md`, `package-model.md`, and `system-settings.md` (Low)

**Category**: Missing cross-references
**Status**: OPEN

`cvss-scoring.md` §Cross-references omits `ticket-mutations.md`, `package-model.md`, and `system-settings.md` — three documents with significant functional dependencies referenced extensively in the spec body.

---

## Design

_Not yet reviewed._

---

## Security

_Not yet reviewed._

---

## API Conventions

_Not yet reviewed._
