# Review: fetcher-infrastructure

**Spec**: `docs/features/platform/fetcher-infrastructure.md`
**Last reviewed**: 2026-06-25
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions, Documentation

> Post-split re-review (fetcher-infrastructure split, Phase 4m). After the
> document was reduced to the generic `BaseFetcher` core (HTTP/TLS, CVE, and
> git content moved to sibling specs), Coherence and Documentation reviewers
> were re-run to verify the consolidation. New findings are recorded below
> (FEI-COH-* and FEI-DOC-*). The split was content-preserving.

---

## Gap Analysis

### FEI-GAP-001 — No specification for partial status transition logic (Medium)

**Status**: RESOLVED — Added explicit status determination precedence rule in BaseFetcher Base Class section and clarifying note in FetcherRunStatus enum (2026-05-28)

### FEI-GAP-002 — Race condition window between API concurrency check and task execution (Medium)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-28)

### FEI-GAP-004 — No behavior specified for duplicate fetcher names at registration (Medium)

**Status**: RESOLVED — Added explicit duplicate name enforcement rule at import time (2026-05-28)

### FEI-GAP-005 — No specification for what happens when FetcherConfig.custom_settings contains invalid values (Medium)

**Status**: RESOLVED — Added fail-fast runtime validation for stored settings against schema (2026-05-28)

### FEI-GAP-008 — fetch_single error handling not specified for non-FetcherError exceptions (Medium)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes — spec now includes comprehensive fetch_single signaling convention, retry policy, and error categorization (2026-05-28)

### FEI-GAP-009 — FetcherConfig auto-creation race condition on first multi-worker startup (Medium)

**Status**: RESOLVED — Spec updated: added explicit idempotent creation requirement (INSERT ... ON CONFLICT DO NOTHING) to FetcherConfig section (2026-05-28)

### FEI-GAP-003 — FetcherRun records for runs that fail during creation (Low)

**Status**: RESOLVED — Spec updated: documented FetcherRun INSERT failure behavior with CRITICAL log and immediate task abort (2026-05-28)

### FEI-GAP-006 — Aggregation behavior for runs with status 'running' that are never resolved (Low)

**Status**: RESOLVED — Spec updated: documented orphaned run force-resolution during aggregation (2026-05-28)

### FEI-GAP-007 — No specification for metric counter overflow or reset between runs (Low)

**Status**: RESOLVED — Spec updated: documented metric counter reset at start of each run() (2026-05-28)

### FEI-GAP-018 — FetcherRun retrieval failure undocumented for API-trigger flow (Low)

**Status**: RESOLVED — Added "FetcherRun retrieval failure" section documenting DB-unreachable and record-not-found cases in fetcher-infrastructure.md (2026-05-29)

---

## Coherence

_No findings (2026-05-28 round)._

### FEI-COH-001 — Dangling intra-document reference to the renamed "Shared HTTP Client" section (Medium)

**Status**: RESOLVED — Item 5 of BaseFetcher Base Class referenced a "Shared
HTTP Client" section that no longer exists in this document after the split
(its content moved to `networking.md`; the surviving local section is
"BaseFetcher HTTP Client Integration"). Updated to point to the local
section and to `networking.md` (2026-06-25)

### FEI-COH-002 — Self-description omits retained HTTP Client Integration content (Low)

**Status**: RESOLVED — The Purpose paragraph and the "This document" row of
the Related Specifications table omitted the "BaseFetcher HTTP Client
Integration" content that the document still owns post-split. Both updated
to enumerate it (2026-06-25)

### FEI-COH-003 — Overloaded term "cursor" across sibling specs (Low)

**Status**: OPEN

`cve-fetcher-infrastructure.md` classifies NVD/GHSA as "Cursor-based (API
with timestamp/cursor)", while `git-fetcher-infrastructure.md` and
`data-model.md` state NVD uses `started_at` and leaves the `cursor` JSONB
column NULL. "Cursor" denotes both the conceptual incremental position and
the `FetcherRun.cursor` column. Reconcilable on careful reading, but the
divergent wording in now-adjacent sibling specs invites confusion. A
one-line clarification distinguishing "conceptual cursor" from the JSONB
column would help. (Tracked here as the consolidation owner; see also
CFI-COH-02.)

### FEI-COH-004 — `data-sources.md` GHSA status mismatch (Low)

**Status**: OPEN

`data-sources.md` summary table says GHSA = "Specified" while its prose says
"Planned". Pre-existing and unrelated to the fetcher split (internal to
`data-sources.md`); flagged for completeness only.

---

## Design

### FEI-DES-001 — Custom settings schema is a bespoke validation framework (Medium)

**Status**: RESOLVED — Spec rewritten: replaced bespoke dictionary DSL with Pydantic BaseModel inner class for settings declaration, leveraging native validation, JSON Schema generation, and IDE support (2026-05-28)

### FEI-DES-002 — Concurrency control race window between API check and task execution (Medium)

**Status**: RESOLVED — Cross-agent duplicate of FEI-GAP-002 (2026-05-28)

### FEI-DES-003 — Weekly aggregation loses error diagnostic information permanently (Low)

**Status**: RESOLVED — Accepted risk: 90-day diagnostic data loss is a calculated trade-off for storage simplicity (2026-05-28)

### FEI-DES-004 — fetch_single invoked in parallel across all CVE fetchers without coordination (Low)

**Status**: RESOLVED — Auto-resolved: cross-source write coordination now documented in cve-service.md (SELECT FOR UPDATE) and cve-tracking.md (SET NX), making this fetcher-infrastructure concern fully addressed (2026-05-29)

### FEI-DES-005 — Enabled check skips silently without any observability (Low)

**Status**: RESOLVED — Spec updated: added DEBUG log on disabled fetcher skip (2026-05-28)

---

## Security

### FEI-SEC-001 — Fetcher dashboard exposes error_message to unauthenticated users (Medium)

**Status**: RESOLVED — Accepted risk: public error_message visibility is a calculated trade-off for operational transparency (2026-05-28)

### FEI-SEC-002 — No rate limiting on manual fetcher trigger endpoint (Medium)

**Status**: RESOLVED — Accepted risk: manual trigger rate limiting is a calculated trade-off; concurrency control and capability restriction provide sufficient protection (2026-05-28)

### FEI-SEC-003 — TOCTOU in API-level concurrency check (Low)

**Status**: RESOLVED — Cross-agent duplicate of FEI-GAP-002 (2026-05-28)

### FEI-SEC-004 — timeout_seconds=0 disables stale run detection permanently (Low)

**Status**: RESOLVED — Spec updated: added warning in API response when timeout_seconds=0 (2026-05-28)

### FEI-SEC-005 — Custom settings validation lacks string length bounds (Low)

**Status**: RESOLVED — Resolved by FEI-DES-001: Pydantic Field(max_length=...) provides native string length bounds, eliminating the gap (2026-05-28)

---

## API Conventions

_No findings._

---

## Documentation

> Documentation reviewer findings from the post-split round (2026-06-25).
> The dangling-reference items overlap with FEI-COH-001/002 and the sibling
> specs' DOC findings; all were fixed in the same change.

### FEI-DOC-001 — Broken internal reference to moved "Shared HTTP Client" content (Medium)

**Status**: RESOLVED — Same defect as FEI-COH-001; the BaseFetcher Base
Class item-5 pointer was corrected to the local "BaseFetcher HTTP Client
Integration" section and `networking.md` (2026-06-25)

### FEI-DOC-002 — Purpose intro under-enumerates retained content (Low)

**Status**: RESOLVED — Purpose paragraph updated to mention catch_up
mechanism, custom settings, error sanitization, and HTTP client integration
(2026-06-25)

### FEI-DOC-003 — "Related Specifications" row omits HTTP Client Integration (Low)

**Status**: RESOLVED — "This document" row updated to include BaseFetcher
HTTP client integration (2026-06-25)

### FEI-DOC-004 — Soft cross-doc references in sibling `git-fetcher-infrastructure.md` (Low)

**Status**: RESOLVED — The sibling references to "Recovery Strategy" and
"Status determination precedence … in the BaseFetcher section" were
corrected to name the actual headings and the owning document
(`fetcher-infrastructure.md`); see GFI-DOC-03 and GFI-DOC-04 (2026-06-25)
