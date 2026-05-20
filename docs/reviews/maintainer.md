# Review: maintainer

**Spec**: `docs/features/packages/maintainer.md`
**Last reviewed**: 2026-05-20
**Reviewers**: Gap Analysis, Coherence, API Conventions

---

## Gap Analysis

### MNT-GAP-01 — Confidentiality filtering not specified (High)

**Category**: Security / data exposure
**Status**: OPEN

The `tickets.md` spec (lines 1180–1187) explicitly requires that maintainer
endpoints apply the same confidentiality filtering as the ticket list. However,
`maintainer.md` does not mention confidential tickets at all. An implementer
working from this spec alone would miss the requirement, potentially exposing
pending/in-progress/completed items from confidential tickets to unauthorized
maintainers.

### MNT-GAP-02 — `analyzed_at` field has no source column (Medium)

**Category**: Data derivation
**Status**: OPEN

The Pending Fixes response includes an `analyzed_at` field described as "When
the ticket entered `Analyzed` status." However, the Ticket table has no
`analyzed_at` column — only `created_at` and `updated_at`. The value would
need to be derived from `TicketAuditEvent` (most recent `status_change` to
`analyzed`). Additionally, tickets can oscillate between Analysis and Analyzed,
making "when it entered" ambiguous (first time? most recent?). This directly
affects the `waiting` sort semantics.

### MNT-GAP-03 — `released_at` source ambiguous for codestream view (Medium)

**Category**: Data derivation
**Status**: OPEN

The Completed response includes `released_at`, but this column exists only on
`TicketPackageProduct` (product-level), not on `TicketPackageTrack`
(codestream-level). The spec's view is codestream-centric. When a track
qualifies via an accepted ReleaseRequest (condition 2), it is unclear which
timestamp is used. The spec should clarify the derivation.

### MNT-GAP-04 — `package` filter matching strategy unspecified (Medium)

**Category**: Boundary conditions
**Status**: OPEN

All three list endpoints accept a `package` query parameter. The spec does not
state whether it performs exact matching or substring/prefix matching. For a
maintainer of `kernel-default`, `kernel-source`, `kernel-syms`, the difference
is significant.

### MNT-GAP-05 — `submission_chain` shape when no SR/RR exist (Medium)

**Category**: Data lifecycle
**Status**: OPEN

A track can become `FIXED + RELEASED` without any SubmissionRequest or
ReleaseRequest records (e.g., VA manually set FIXED, or release detected
before submission tracking). The spec does not state what the
`submission_chain` object looks like in this case — `null`? An object with
all null fields? API consumers need to know.

### MNT-GAP-06 — Per-ticket Resolved error state hides completed work (Medium)

**Category**: User-facing scenarios
**Status**: OPEN

When a ticket is Resolved, the per-ticket endpoint returns `error_state:
resolved`. But a Resolved ticket has completed tracks the maintainer should
see to confirm their work was released. Returning an error state instead of
showing the completed data means the maintainer loses visibility. The spec
should justify the error state or show completed data for resolved tickets.

### MNT-GAP-07 — Per-ticket endpoint confidentiality check missing (Medium)

**Category**: Security
**Status**: OPEN

The evaluation order for the per-ticket endpoint goes from "ticket does not
exist → 404" to "ticket is soft-deleted → 410" with no confidentiality check.
If a confidential ticket exists, a non-authorized authenticated user would
see a 200 with error_state instead of a 404 — leaking the ticket's existence.
The endpoint is at `/api/v1/my/packages/ticket/{ticket_id}`, which is NOT
under `/api/v1/tickets/{ticket_id}/` and therefore not covered by the scoped
Ticket Accessibility Check.

### MNT-GAP-08 — `days` parameter validation unspecified (Low)

**Category**: Boundary conditions
**Status**: OPEN

The `days` parameter on `/completed` has no stated valid range. Behavior for
`days=0`, negative values, or extremely large values (e.g., 3650) is
undefined.

### MNT-GAP-09 — Ticket {ticket_id} dual-lookup not confirmed (Low)

**Category**: Ambiguity
**Status**: OPEN

The tickets spec defines dual-lookup (UUID or `SNTL-{n}`) for all endpoints
accepting `{ticket_id}`. This endpoint is under `/my/packages/`, not
`/tickets/`. The spec should confirm dual-lookup applies here.

### MNT-GAP-10 — Invalid sort_by/sort_order behavior unspecified (Low)

**Category**: Boundary conditions
**Status**: OPEN

The spec does not state what happens when an invalid `sort_by` value (e.g.,
`foobar`) or invalid `sort_order` (e.g., `random`) is provided. Should return
422 validation error.

### MNT-GAP-11 — Bugowner cache staleness not acknowledged (Low)

**Category**: Data lifecycle
**Status**: OPEN

Bugowner data is updated every 14 days. During staleness windows, a former
maintainer sees packages they no longer maintain, and the new maintainer does
not. The spec could mention this for user expectation setting.

### MNT-GAP-12 — Tickets without CVE in Pending response (Low)

**Category**: User-facing scenarios
**Status**: OPEN

Tickets without a CVE can reach Analyzed status (via `severity_override`).
They appear in Pending with `cve_id: null`. The maintainer has no description
of what security issue they're fixing — only `ticket_id` and
`ticket_sequence_id`. This is a known limitation of the compact response.

### MNT-GAP-13 — List endpoints missing error documentation (Low)

**Category**: Documentation
**Status**: OPEN

The three paginated list endpoints have no error status table. They should at
minimum state "No endpoint-specific error responses. See `docs/api-spec.md`
for global responses (401, 422, 500)."

---

## Coherence

### MNT-COH-01 — Severity enum uses `moderate` instead of `medium` (Medium)

**Category**: Terminology conflict
**Status**: OPEN

The maintainer spec's response schema lists severity values as "critical,
high, moderate, low." But `tickets.md` (line 1377) and `data-model.md` both
use `medium`. This is a naming conflict that would cause serialization bugs.
Must be corrected to `medium`.

### MNT-COH-02 — Pending filter restricts to Analyzed tickets only (Medium)

**Category**: Business rule consistency
**Status**: OPEN

The Pending Fixes filter requires `Ticket.status = Analyzed`. However, a
ticket in `Analysis` can also have tracks with `status = AFFECTED` (VA has
set some tracks but hasn't completed all gates). A maintainer would NOT see
pending fixes until all gates are met — potentially hours or days of delay.
The spec claims to provide "immediate visibility into what needs fixing" but
visibility is gated on full analysis completion. The spec should acknowledge
this design choice explicitly.

### MNT-COH-03 — Example value for `reference` field is abbreviated (Low)

**Category**: Data inconsistency
**Status**: OPEN

The response example uses `"reference": "SLE-15-SP6"` but the database stores
the full IBS codestream project name (e.g., `SUSE:SLE-15-SP6:Update`) per
`package-model.md` (line 245). The example value does not match the
expected stored format.

### MNT-COH-04 — `analyzed_at` derivation crosses spec boundaries (Medium)

**Category**: Cross-spec dependency
**Status**: OPEN — Cross-reference of MNT-GAP-02. The field requires querying
`TicketAuditEvent` (owned by `ticket-audit-log.md`) for the most recent
status_change to `analyzed`. This cross-spec data dependency and its
performance implications should be documented.

---

## API Conventions

### MNT-API-01 — `days` parameter deviates from standard date range pattern (Medium)

**Category**: Non-standard filter
**Status**: OPEN

The `days` parameter on `/completed` is a relative date range (integer days
lookback). The standard convention in `api-spec.md` is `from_date` / `to_date`.
Either replace with `from_date` for consistency, or justify the deviation
(e.g., the completed view is always a lookback window from current time).

### MNT-API-02 — Per-ticket confidentiality gap (Medium)

**Category**: Security
**Status**: OPEN — Cross-reference of MNT-GAP-07. The endpoint is not under
`/api/v1/tickets/{ticket_id}/` and thus not covered by the Ticket
Accessibility Check scoped response. Must implement its own confidentiality
check.

### MNT-API-03 — Per-ticket `error_state` pattern not documented in api-spec (Low)

**Category**: Convention deviation
**Status**: OPEN

The per-ticket endpoint returns 200 OK with `{"data": {"error_state": {...}}}`
for non-error conditions (not-analyzed, not-bugowner, resolved). This is an
unconventional response pattern (200 with a discriminated union). While
internally consistent, the pattern should have a brief justification note.

### MNT-API-04 — `per_page` max not restated (Low)

**Category**: Documentation
**Status**: OPEN

The global `per_page` max (100) from `api-spec.md` is not referenced in the
endpoint parameter tables. While the global convention applies, stating it
reduces ambiguity.
