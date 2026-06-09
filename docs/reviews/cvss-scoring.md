# Review: cvss-scoring

**Spec**: `docs/features/tickets/cvss-scoring.md`
**Last reviewed**: 2026-06-09
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### CVS-GAP-10 — SUSE CVSS POST endpoint specifies upsert but service function rejects duplicates (Medium)

**Category**: Error and failure paths
**Status**: OPEN

The spec says at line 493-494 "If an existing SUSE assessment for the derived
version exists, it is updated (upsert)." However, ticket-mutations.md defines
`create_cvss_assessment()` with the precondition: "No existing assessment for
the same (CVE, provider, version) combination — raises
`DuplicateCVSSAssessmentError` (HTTP 409)". The API endpoint promises upsert
semantics while the service function rejects duplicates. An implementer would
need to decide whether the endpoint handler checks for existing records and
dispatches to `update_cvss_assessment()` instead, or whether the service
implements upsert internally.

### CVS-GAP-11 — CVSS v2.0 inclusion in Severity Resolution Cascade contradicts Key Principle 2 (Medium)

**Category**: State machine completeness
**Status**: OPEN

Key Principle 2 states: "Other versions (e.g., v2.0) may arrive from external
sources and are stored and displayed but not used for decisions." However, the
Severity Resolution Cascade step 4 says: "If at least one external provider has
an assessment for any non-default version, use the highest score among those."
If the default version is "3.1" and the only external assessment available is a
CVSS v2.0 score from Red Hat, step 4 would resolve it as the severity score —
making a v2.0 score the basis for a severity decision. The spec should clarify
whether steps 2 and 4 restrict "other version" candidates to supported versions
(v3.1 and v4.0) or include all stored versions including v2.0.

### CVS-GAP-13 — CVSS v3.0 vectors accepted by endpoint but v3.1 required by gate (Medium)

**Category**: User-facing scenario gaps
**Status**: OPEN

The spec says the CVSS version is derived from the vector prefix (CVSS:3.0/ →
3.0). The gate requires both SUSE CVSS v3.1 AND v4.0 assessments. If a VA
submits a CVSS:3.0 vector, the system would create a v3.0 assessment, but the
gate still requires v3.1. The VA would not understand why the ticket won't
progress. The spec does not specify whether v3.0 vectors should be rejected for
SUSE assessments, v3.0 should be treated as equivalent to v3.1 for gate
purposes, or the system should provide guidance.

### CVS-GAP-12 — Severity and eligibility response objects have unspecified null/absent structure (Low)

**Category**: Boundary conditions
**Status**: OPEN

The GET /api/v1/cves/{cve_id}/cvss response includes severity and eligibility
objects but does not define their structure when the resolution produces no
result. When severity is absent, is the severity object null, or is it
{score: null, provider: null, label: "none"}? The severity.provider value
format is also unspecified. The eligibility object always returns a score
(10.0 fallback), but should eligibility.source be "fallback" in this case?

### CVS-GAP-14 — SUSE-defined severity thresholds per version referenced but never defined (Low)

**Category**: Configuration and defaults
**Status**: OPEN

The spec says "Sentinel uses SUSE-defined severity thresholds per version.
Until explicit thresholds are configured, the standard CVSS thresholds for each
version apply." This implies a configurable per-version threshold mechanism, but
no specification defines it: absent from system-settings.md, absent from the
data model, no API or admin UI described. If this is a future item it should be
marked as such; if current, the mechanism is missing.

### CVS-GAP-15 — Resolved CVSS version not included in severity response object (Low)

**Category**: User-facing scenario gaps
**Status**: OPEN

The severity response object includes score, provider, and label but not the
CVSS version of the resolved score. When the resolved score comes from a
non-default version, severity is mapped using that version's rating scale. An
API consumer seeing {score: 7.5, label: High} cannot determine whether this was
scored on v3.1 or v4.0 scale.

### CVS-GAP-03 — No explicit specification for batch recalculation when SUSE has old-default-version assessment only (High)

**Status**: RESOLVED — Auto-resolved: the spec already covers this scenario — "SUSE has not scored the default version" is listed as an explicit condition in Eligibility Score Resolution (line 90), and the gate requiring both v3.1 and v4.0 for Analysis→Analyzed limits the impact to in-progress Analysis tickets, which is expected behavior (2026-06-07)

### CVS-GAP-08 — `sync_redhat_cves` fetcher error handling explicitly marked TBD (High)

**Status**: RESOLVED — Error handling fully specified in cve-tracking.md (Fetcher: sync_redhat_cves, Error Handling section): HTTP 404 and no-CVSS-fields map to missing (existing assessments preserved), HTTP 429/5xx/timeout retry 3x then failure, unparseable data is non-retryable failure, batch run uses per-CVE error handling with 3-consecutive-failure abort (2026-06-08)

### CVS-GAP-01 — `resolve_eligibility_score` input contract does not specify pre-filtering of assessments (Medium)

**Status**: RESOLVED — Input contract clarified: both functions receive full unfiltered assessments; filtering is internal (2026-06-08)

### CVS-GAP-02 — Batch recalculation for default version change has no named entry point in `ticket_mutations` (Medium)

**Status**: RESOLVED — Defined recalculate_cvss_chain() in ticket-mutations.md as dedicated entry point for batch recalculation; updated cvss-scoring.md and system-settings.md references (2026-06-08)

### CVS-GAP-04 — Step numbering 4 and 4b creates ordering ambiguity for `CVE.severity` update (Medium)

**Status**: RESOLVED — Write-path section rewritten as conceptual overview referencing ticket-mutations.md; eliminates step numbering ambiguity (2026-06-08)

### CVS-GAP-05 — Recalculation Chain note on Resolved tickets inconsistent with `ensure_ticket_operable()` semantics (Medium)

**Status**: RESOLVED — Auto-resolved: behavior for Resolved tickets now explicitly specified in both cvss-scoring.md and ticket-mutations.md (reconcile deterministic gate evaluation, backward transitions documented) (2026-06-08)

### CVS-GAP-06 — Concurrency gap in batch recalculation for concurrent default version changes (Medium)

**Status**: RESOLVED — Specified singleton Redis lock with read-after-lock pattern for batch recalculation; added mandatory default_cvss_version parameter to recalculate_cvss_chain() (2026-06-08)

### CVS-GAP-16 — DELETE endpoint cvss_version path parameter has no format validation specified (Low)

**Status**: RESOLVED — Cross-agent duplicate of CVS-API-05 (2026-06-09)

### CVS-GAP-07 — `GET /cves/{cve_id}/cvss` exposes severity cascade resolved fields but no eligibility score (Low)

**Status**: RESOLVED — Restructured /cvss response with nested severity and eligibility objects; eligibility.source disambiguates SUSE assessment vs 10.0 fallback (2026-06-08)

### CVS-GAP-09 — Batch recalculation scope for soft-deleted products not explicitly confirmed (Low)

**Status**: RESOLVED — Added explicit parenthetical reference to package-model.md DD8 confirming soft-deleted products are included in eligibility recalculation scope (2026-06-08)

---

## Coherence

### CVS-COH-11 — `severity_changed` audit event `user_id` definition contradicts between data-model.md and ticket-audit-log.md/ticket-mutations.md (Medium)

**Category**: Contradictory definitions
**Status**: OPEN

data-model.md TicketAuditEventType table states that `severity_changed` has
`user_id` that is "always NULL (system event)". However, ticket-audit-log.md
defines `severity_changed` as: "NULL for automatic CVSS recalculation, acting
user's UUID for manual severity override (`set_severity_override()`)".
ticket-mutations.md confirms at line 581: `set_severity_override` creates a
`TicketAuditEvent` with `user_id = acting_user_id`. The data-model.md
description is incorrect.

### CVS-COH-12 — DELETE endpoint error table uses `RESOURCE_NOT_FOUND` but ticket-mutations.md defines `CVSS_ASSESSMENT_NOT_FOUND` (Medium)

**Category**: Contradictory definitions
**Status**: OPEN

In cvss-scoring.md the DELETE endpoint error table lists 404
`RESOURCE_NOT_FOUND` for "No SUSE assessment exists for the specified version".
However, ticket-mutations.md defines `CVSSAssessmentNotFoundError` with error
code `CVSS_ASSESSMENT_NOT_FOUND` (HTTP 404). Since the DELETE endpoint routes
through `ticket_mutations.delete_cvss_assessment()`, the error code surfaced
should be `CVSS_ASSESSMENT_NOT_FOUND`, not the generic `RESOURCE_NOT_FOUND`.

### CVS-COH-13 — POST endpoint error table omits `CVSS_DUPLICATE_ASSESSMENT` from ticket-mutations.md (Low)

**Category**: Contradictory definitions
**Status**: OPEN

ticket-mutations.md defines `DuplicateCVSSAssessmentError` with HTTP 409 and
code `CVSS_DUPLICATE_ASSESSMENT`. The cvss-scoring.md POST endpoint does not
list this error. Since the POST endpoint uses upsert semantics for SUSE
assessments, `CVSS_DUPLICATE_ASSESSMENT` may not apply — but the spec should
explicitly clarify.

### CVS-COH-14 — GET response example shows score 8.1 for a CVSS:3.1 vector that computes to 9.8 (Low)

**Category**: Contradictory definitions
**Status**: OPEN

The GET endpoint response example shows a CVSS vector
CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H with score: 8.1. However, this
vector actually computes to a base score of 9.8. The severity.label shows
"High" (which corresponds to 8.1) but should show "Critical" for a 9.8 score.

### CVS-COH-06 — Recalculation Chain step 2 implies direct eligibility writes; must route through `package_service` (High)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-06-08)

### CVS-COH-01 — Eligibility Threshold section contradicts Eligibility Score Resolution (Medium)

**Status**: RESOLVED — Eligibility cascade aligned to 2-step SUSE-only; resolve_cvss_score renamed to resolve_severity_score; resolve_eligibility_score added as dedicated function (2026-06-03)

### CVS-COH-04 — Recalculation Chain audit trail omits `cvss_assessment_changed` event (Medium)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-06-08)

### CVS-COH-07 — Recalculation Chain step 3 note overstates restriction as architectural invariant (Medium)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-06-08)

### CVS-COH-02 — Write-path flow uses non-standard step label `4b` instead of renumbered sequence (Low)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-06-08)

### CVS-COH-03 — `auto_assign_actor()` call absent from CVSS write-path summary (Low)

**Status**: RESOLVED — Auto-resolved: write-path section rewritten as conceptual overview; no longer lists implementation steps (2026-06-08)

### CVS-COH-05 — When Severity is Recalculated lists a redundant trigger (Low)

**Status**: RESOLVED — Removed redundant SUSE-specific trigger; already covered by the general "CVSS assessment added/modified/removed" trigger (2026-06-08)

### CVS-COH-08 — Term "cascade" overloaded across Severity Resolution Cascade and Recalculation Chain (Low)

**Status**: RESOLVED — Terminology disambiguated: cascade reserved for resolution strategies, chain for propagation, flattening for duplicate pointer resolution. Convention added to docs/conventions.md. ~179 lines renamed across 21 files (2026-06-08)

### CVS-COH-09 — `product-lifecycle-transitions.md` implies async path where `cvss-scoring.md` describes synchronous path (Low)

**Status**: RESOLVED — Removed cvss_change from re_evaluate_product_eligibility reasons; added boundary note clarifying sync path via ticket_mutations (2026-06-08)

### CVS-COH-10 — Cross-references omits `ticket-mutations.md`, `package-model.md`, and `system-settings.md` (Low)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-06-08)

---

## Design

### CVS-DES-02 — API upsert endpoint inconsistent with service layer's separate create/update functions (Medium)

**Category**: Architectural fitness
**Status**: OPEN

POST /api/v1/cves/{cve_id}/cvss/suse performs upsert but ticket-mutations.md
defines separate `create_cvss_assessment()` and `update_cvss_assessment()` with
different preconditions. The spec should document the internal dispatch logic.

### CVS-DES-03 — Batch CVSS recalculation has no defined result storage or admin feedback mechanism (Medium)

**Category**: Complexity vs simplicity
**Status**: OPEN

The batch task "reports the total number of tickets processed, successes, and
failures" but doesn't define where this report is stored or how it reaches the
admin.

### CVS-DES-05 — Recalculation Chain description broader than `recalculate_cvss_chain()` specification (Low)

**Category**: Architectural fitness
**Status**: OPEN

The Recalculation Chain says "every TicketPackageProduct" but
`recalculate_cvss_chain()` correctly specifies exclusions for
`is_eligible_override=true` and Reactive LTSS.

### CVS-DES-06 — Redis lock crash scenario produces misleading error message and no automatic recovery (Low)

**Category**: Edge cases and risks
**Status**: OPEN

If worker crashes, the lock persists for 2 hours. The error message "anomaly: a
batch should not take that long" is misleading. No mechanism for manual force
recalculation.

### CVS-DES-07 — Severity and eligibility response objects may be stale on read path for CVEs without active tickets (Low)

**Category**: Edge cases and risks
**Status**: OPEN

For CVEs with Resolved tickets, assessment data may be months old. The API
response doesn't indicate staleness.

### CVS-DES-01 — Ambiguous version tie-breaking in severity cascade allows v2.0 participation despite stated exclusion (High)

**Status**: RESOLVED — Cross-agent duplicate of CVS-GAP-11 (2026-06-09)

### CVS-DES-04 — Cross-version severity mapping references undefined custom SUSE thresholds (Low)

**Status**: RESOLVED — Cross-agent duplicate of CVS-GAP-14 (2026-06-09)

---

## Security

### CVS-SEC-01 — Public CVSS endpoint may expose eligibility data during CVE-to-confidential-ticket transition (Medium)

**Category**: Data Exposure
**Status**: OPEN

GET /api/v1/cves/{cve_id}/cvss is public and returns eligibility score data. A
CVE transitioning from ticketless to confidential-ticket-linked has a window
where previously served eligibility data may have been cached.

### CVS-SEC-02 — No rate limiting on CVSS mutation endpoints that trigger expensive recalculation chains (Medium)

**Category**: Denial of Service
**Status**: OPEN

POST /api/v1/cves/{cve_id}/cvss/suse triggers full recalculation chain on
every call. A compromised `manage_cvss` account could rapidly submit changes
for resource exhaustion.

### CVS-SEC-04 — No explicit input length constraint on CVSS vector string in request schema (Low)

**Category**: Input Validation
**Status**: OPEN

POST endpoint accepts vector string with no schema-level maximum length. DB
column is VARCHAR(200) but spec should mandate Pydantic schema constraint.

### CVS-SEC-05 — CVSS library error handling and input sanitization not specified (Low)

**Category**: Input Validation
**Status**: OPEN

Spec relies on cvss library for parsing but doesn't specify how unexpected
exceptions or adversarial inputs are handled.

### CVS-SEC-06 — Unbounded public CVSS GET response relies on implicit natural bound (Low)

**Category**: Resource Consumption
**Status**: OPEN

GET returns all assessments without pagination. Provider cardinality is
implicitly bounded but not constrained.

### CVS-SEC-07 — No audit trail for failed CVSS mutation attempts (Low)

**Category**: Audit Trail
**Status**: OPEN

Failed mutation attempts are not logged in the ticket audit trail.

### CVS-SEC-03 — Batch recalculation lock failure may silently leave tickets in stale state (Low)

**Status**: RESOLVED — Cross-agent duplicate of CVS-DES-06 (2026-06-09)

---

## API Conventions

### CVS-API-01 — POST endpoint uses wrong HTTP method for upsert semantics (Medium)

**Category**: Mutation Patterns
**Status**: OPEN

POST performs upsert but the resource ambiguity (version derived from vector
prefix) creates a borderline case. Spec should explicitly justify POST over
PATCH.

### CVS-API-02 — GET endpoint response uses non-standard data wrapper structure (Medium)

**Category**: Response Envelope
**Status**: OPEN

GET response has data containing an object with nested keys (assessments,
default_cvss_version, severity, eligibility) rather than the list directly.
Mixed resource data with computed metadata.

### CVS-API-03 — Authorization format inconsistency (Low)

**Category**: Authorization Declaration
**Status**: OPEN

POST/DELETE use bold+code formatting for capability declaration. GET declares
access in prose rather than structured format.

### CVS-API-04 — GET endpoint missing explicit sorting statement (Low)

**Category**: Sorting
**Status**: OPEN

Unpaginated endpoint does not state whether client-controlled sorting is
supported or intentionally omitted.

### CVS-API-05 — DELETE endpoint path uses cvss_version which may accept ambiguous values (Low)

**Category**: Path Naming
**Status**: OPEN

cvss_version path parameter lacks documentation of valid values. Behavior for
unrecognized version string (404 vs 422) is unspecified.
