# Terminology Rename: "cascade" → "chain" / "flattening" for Propagation Concepts

**Origin**: review finding CVS-COH-08 identified that the term "cascade"
is overloaded in the documentation. It is used for two semantically
distinct concepts:

1. **Resolution strategy** (prioritized fallback sequence): "Severity
   Resolution Cascade" — a 5-step algorithm that tries providers in
   priority order until a score is found. This usage is correct and
   widely understood.

2. **Propagation of side effects** (chain of derived updates): used for
   four distinct propagation patterns across the codebase:
   - "Recalculation Cascade" — CVSS change triggers severity
     recalculation → eligibility recalculation → ticket status
     reconciliation
   - "Duplicate cascade" — marking a ticket as duplicate triggers
     re-pointing of other tickets that referenced it
   - "Orphan cascade" — soft-deleting the last product triggers track
     soft-deletion, which may trigger further cleanup
   - "Deactivation cascades" — deactivating an AD employee triggers
     API key revocation → ticket reassignment → session invalidation

**Decision**: reserve "cascade" exclusively for the resolution strategy
concept. Rename propagation/side-effect usages as follows:

- Groups 1, 3, 4 → **"chain"** (general propagation term)
- Group 2 (duplicate) → **"flattening"** (avoids collision with the
  pre-existing "duplicate chain" term used for the `duplicate_of_id`
  linked-list data structure in `tickets.md`)

---

## Rename Map

### Terminology

| Current term | New term |
|---|---|
| Recalculation Cascade | Recalculation Chain |
| Cascade Execution Model | Chain Execution Model |
| recalculation cascade (in prose) | recalculation chain |
| duplicate cascade (in prose) | duplicate flattening |
| orphan cascade (in prose) | orphan chain |
| Deactivation cascades (in prose) | Deactivation chain |
| cascade operations / cascade phase / cascade step (in duplicate context) | flattening operations / flattening phase / flattening step |
| cascade-updated (in duplicate context) | flattening-updated |

### Function Names

| Current name | New name |
|---|---|
| `recalculate_cvss_cascade()` | `recalculate_cvss_chain()` |
| `execute_duplicate_cascade()` | `execute_duplicate_flattening()` |

### Parameter and Field Names

| Current name | Context | New name |
|---|---|---|
| `cascade_ticket_ids` | Parameter in `execute_duplicate_flattening`, field in `MarkAsDuplicateResult` | `flattening_ticket_ids` |
| `"cascade": [...]` | JSON response field in soft-delete endpoints (`package-model.md`) | `"orphan_cleanup": [...]` |

### Mermaid Diagram Identifiers

The `system-map.md` Mermaid diagram uses `CASCADE` as a node ID for the
eligibility evaluation step. The node **label** ("Eligibility cascade
(product thresholds)") uses "cascade" in the preserved resolution
strategy sense and stays unchanged. Only the bare Mermaid identifiers
are renamed to avoid confusion with the overloaded term:

| Current | New | Lines |
|---------|-----|-------|
| `CASCADE["Eligibility cascade<br/>(product thresholds)"]` | `ELIG_EVAL["Eligibility cascade<br/>(product thresholds)"]` | 449 |
| `SEV --> CASCADE` | `SEV --> ELIG_EVAL` | 466 |
| `CASCADE --> EVENT` | `ELIG_EVAL --> EVENT` | 468 |

### Section Headings

| Current heading | File | New heading |
|---|---|---|
| `## Recalculation Cascade` | `cvss-scoring.md` | `## Recalculation Chain` |
| `## Cascade Execution Model` | `cvss-scoring.md` | `## Chain Execution Model` |
| `### recalculate_cvss_cascade()` | `ticket-mutations.md` | `### recalculate_cvss_chain()` |
| `### Exception: CVSS Recalculation Cascade Eligibility Mutations` | `ticket-mutations.md` | `### Exception: CVSS Recalculation Chain Eligibility Mutations` |
| `### execute_duplicate_cascade` | `ticket-service.md` | `### execute_duplicate_flattening` |
| `#### Cascade as Best-Effort Flattening` | `tickets.md` | `#### Best-Effort Flattening` |
| `### Cascading composition` | `package-service.md` | `### Chain composition` |
| `## Cascading Cleanup` | `product-lifecycle-transitions.md` | `## Orphan Cleanup` |

### Preserved (no change)

These usages of "cascade" refer to the resolution strategy concept or
to standard SQL semantics and MUST NOT be renamed:

**Resolution strategy references:**

- "Severity Resolution Cascade" (section heading and cross-references)
- "5-step cascade", "2-step SUSE-only cascade" (inline descriptions)
- `resolve_severity_score` / `resolve_eligibility_score` documentation
- "resolution cascade" when referring to the score selection algorithm
- "severity cascade" when referring to the provider fallback steps
- "CVSS resolution cascade" in cross-references sections

**SQL standard:**

- All `ON DELETE CASCADE` constraints in `data-model.md` (~9
  occurrences) and `ticket-references.md` (1 occurrence)

**Matching/priority strategies:**

- "package match cascade" in `architecture.md` and `package-model.md`
  — a matching strategy (title → heuristic → primary.xml)
- "The match is a cascade" in `ibs-product-release-detection.md`
- "priority cascade" in `reviews/ticket-references.md`

**Files with ONLY preserved usages (no changes needed):**

- `docs/features/tickets/cve-tracking.md` (5 occ. — all resolution)
- `docs/features/packages/product-catalog.md` (1 occ. — resolution)
- `docs/features/packages/ibs-product-release-detection.md` (1 occ. —
  match strategy)
- `docs/features/tickets/ticket-references.md` (1 occ. — SQL)
- `docs/architecture.md` (1 occ. — match strategy)
- `docs/reviews/cve-service.md` (4 occ. — resolution)
- `docs/reviews/ticket-references.md` (1 occ. — priority strategy)
- `AGENTS.md` (3 occ. — resolution, in guardrail 13)
- `.opencode/agents/spec-gap-analyzer.md` (1 occ. — generic DB term)

### Edge Cases

- Line 102 of `cvss-scoring.md`: "This cascade is implemented by
  `resolve_eligibility_score`" → rename to "This resolution is
  implemented by..." (refers to the eligibility resolution algorithm,
  not a propagation chain — but uses bare "cascade" which could be
  misread post-rename)
- `docs/data-model.md` column descriptions for `duplicate_of_id` (line
  1044) and `duplicate_target_changed` event (line 1173): these are
  Group 2 renames embedded in data-model table definitions — care
  needed to preserve table formatting
- `docs/features/tickets/ticket-audit-log.md` line 33 and
  `docs/data-model.md` line 1173: both contain the
  `duplicate_target_changed` event description as a single long line
  with 3 occurrences of "cascade" each — all Group 2. Both files must
  be updated together to keep descriptions in sync

---

## Occurrence Inventory

### Group 1: Recalculation cascade → chain (68 lines)

| File | Lines | Details |
|------|-------|---------|
| `docs/features/tickets/cvss-scoring.md` | 22 | `recalculate_cvss_cascade` (9x), headings (2x), prose (11x) |
| `docs/features/tickets/ticket-mutations.md` | 13 | `recalculate_cvss_cascade` (6x), headings (2x), prose (5x) |
| `docs/features/tickets/cve-service.md` | 11 | `recalculate_cvss_cascade` (4x), prose (7x) |
| `docs/features/platform/system-settings.md` | 5 | `recalculate_cvss_cascade` (1x), heading refs (2x), prose (2x) |
| `docs/features/tickets/ticket-service.md` | 4 | `recalculate_cvss_cascade` (3x), prose (1x) |
| `docs/data-model.md` | 2 | "recalculation cascades" (lines 1085, 1089) |
| `docs/reviews/cvss-scoring.md` | 8 | Finding titles and resolution text |
| `docs/features/packages/product-lifecycle-transitions.md` | 1 | Cross-ref to "Recalculation Cascade" heading |
| `docs/features/platform/fetcher-infrastructure.md` | 1 | "standard cascade" (line 476) |
| `docs/reviews/system-settings.md` | 1 | Resolution text |

### Group 2: Duplicate cascade → flattening (67 lines)

| File | Lines | Details |
|------|-------|---------|
| `docs/features/tickets/ticket-service.md` | 31 | `execute_duplicate_cascade` (13x), `cascade_ticket_ids` (8x), heading (1x), prose (9x) |
| `docs/features/tickets/tickets.md` | 15 | Heading (1x), prose (14x) |
| `docs/reviews/ticket-service.md` | 9 | Finding titles and resolution text |
| `docs/drafts/open-points.md` | 5 | `execute_duplicate_cascade` (2x), prose (3x) |
| `docs/features/tickets/ticket-audit-log.md` | 3 | Event type description (1 line, 3x "cascade") |
| `docs/data-model.md` | 2 | Column description (line 1044), event description (line 1173) |
| `docs/features/tickets/ticket-mutations.md` | 1 | Prose (line 301) |
| `docs/reviews/ticket-mutations.md` | 1 | Section reference (line 128) |

### Group 3: Orphan cascade → chain (42 lines)

| File | Lines | Details |
|------|-------|---------|
| `docs/features/packages/package-model.md` | 24 | `"cascade"` JSON field (4x), field prose (10x), orphan cascade prose (10x) |
| `docs/features/packages/package-service.md` | 5 | Heading (1x), prose (4x) |
| `docs/reviews/ticket-mutations.md` | 3 | Finding titles and resolution text |
| `docs/reviews/package-model.md` | 3 | Resolution text |
| `docs/features/packages/product-lifecycle-transitions.md` | 2 | Heading (1x), prose (1x) |
| `docs/features/tickets/ticket-mutations.md` | 2 | Prose (lines 238, 239) |
| `docs/features/tickets/tickets.md` | 1 | Prose (line 226) |
| `docs/reviews/package-service.md` | 2 | Finding title (1x), resolution text (1x) |

### Group 4: Deactivation and miscellaneous propagation (2 lines)

| File | Lines | Details |
|------|-------|---------|
| `docs/features/identity/ad-integration.md` | 1 | "Deactivation cascades" (line 880) |
| `docs/features/identity/user-service.md` | 1 | "cascade/anonymization" (line 143) |

### Summary

**Total: ~179 lines to modify across 21 unique files** (plus 3
Mermaid identifier-only changes in `system-map.md` — see Mermaid
Diagram Identifiers above).

Cross-file summary (files appearing in multiple groups):

| File | G1 | G2 | G3 | G4 | Total |
|------|----|----|----|----|-------|
| `ticket-service.md` | 4 | 31 | — | — | 35 |
| `ticket-mutations.md` | 13 | 1 | 2 | — | 16 |
| `tickets.md` | — | 15 | 1 | — | 16 |
| `data-model.md` | 2 | 2 | — | — | 4 |
| `product-lifecycle-transitions.md` | 1 | — | 2 | — | 3 |
| `reviews/ticket-mutations.md` | — | 1 | 3 | — | 4 |

---

## Execution Plan

### Step 1: Rename Group 1 — Recalculation cascade → chain

Rename all occurrences of `recalculate_cvss_cascade`, "Recalculation
Cascade", "recalculation cascade", "Cascade Execution Model", and
related prose in the 10 files listed above. Apply the edge case fix
on `cvss-scoring.md` line 102 ("This cascade" → "This resolution").

Also rename the Mermaid node ID in `system-map.md` from `CASCADE` to
`ELIG_EVAL` (lines 449, 466, 468) — label text stays unchanged
(preserved resolution strategy reference). `ELIG` is not used because
a node with that ID already exists at line 499 of the same file. See
Mermaid Diagram Identifiers in the Rename Map above.

Verification: grep all 10 files for `recalculate_cvss_cascade`,
`Recalculation Cascade`, and `Cascade Execution Model` — expect zero
matches. Grep `system-map.md` for bare `CASCADE` — expect zero matches.
Grep for bare "cascade" and verify remaining occurrences are either
preserved (resolution strategy) or belong to other groups.

### Step 2: Rename Group 2 — Duplicate cascade → flattening

Rename all occurrences of `execute_duplicate_cascade`,
`cascade_ticket_ids`, "duplicate cascade", and related prose in the
8 files listed above. Use "flattening" (not "chain") to avoid
collision with the pre-existing "duplicate chain" data structure term.
Includes:
- Section heading `#### Cascade as Best-Effort Flattening` →
  `#### Best-Effort Flattening` in `tickets.md`
- Function heading `### execute_duplicate_cascade` →
  `### execute_duplicate_flattening` in `ticket-service.md`
- Parameter `cascade_ticket_ids` → `flattening_ticket_ids`
- Prose "cascade-updated" → "flattening-updated"

Verification: grep the 8 files for `duplicate_cascade`,
`cascade_ticket_ids`, and `Cascade as Best-Effort` — expect zero
matches.

### Step 3: Rename Group 3 — Orphan cascade → chain

Rename all occurrences of "orphan cascade", "Cascading composition",
the `"cascade"` JSON field (→ `"orphan_cleanup"`), and related prose
in the 8 files listed above. Rename the section heading
`## Cascading Cleanup` → `## Orphan Cleanup` in
`product-lifecycle-transitions.md`. The JSON field uses
`"orphan_cleanup"` (not `"chain"`) because it describes the content
(array of ancestors auto-deleted by orphan cleanup), not a sequential
traversal.

Verification: grep the 8 files for "orphan cascade", "Cascading", and
`"cascade"` — expect zero matches (excluding preserved `ON DELETE
CASCADE` and "package match cascade" entries).

### Step 4: Rename Group 4 — Deactivation and miscellaneous

Rename "Deactivation cascades" → "Deactivation chain" in
`ad-integration.md` and "cascade/anonymization" →
"chain/anonymization" in `user-service.md`.

### Step 5: Add terminology convention to `docs/conventions.md`

Add a `### Cascade / Chain / Flattening Terminology` subsection under
"General" in `docs/conventions.md` (alongside the existing AD/LDAP/SSO
terminology section). This serves as the permanent record of the
terminology distinction and prevents future re-introduction of the
ambiguity:

| Term | Concept | Usage |
|------|---------|-------|
| **cascade** | Resolution strategy | Prioritized fallback sequence that tries sources in order until a result is found. Examples: "Severity Resolution Cascade", "package match cascade" |
| **chain** | Propagation of side effects | Sequence of derived mutations triggered by a primary change. Examples: "Recalculation Chain", "Deactivation chain", "orphan chain" |
| **flattening** | Linked-list resolution | Resolution and update of pointer chains. Examples: "duplicate flattening", `execute_duplicate_flattening()` |

Rules:

- Do not use "cascade" for propagation/side-effect sequences.
- The term "chain" in this convention refers exclusively to mutation
  propagation. Pre-existing domain-specific uses of "chain" in other
  contexts are unrelated and unaffected: "duplicate chain" (the
  `duplicate_of_id` linked-list data structure), "submission chain"
  (IBS SR→incident→RR pipeline in `maintainer.md`), "manager chain"
  (reporting hierarchy in `ad-integration.md`), "certificate chain"
  (TLS).

### Step 6: Full-codebase verification

Run a broad grep across all `docs/` and project files for the pattern
"cascade" (case-insensitive). Verify that every remaining occurrence
falls into one of the preserved categories:

- Resolution strategy ("Severity Resolution Cascade", "5-step cascade",
  "2-step cascade", "resolution cascade", "severity cascade")
- SQL standard (`ON DELETE CASCADE`)
- Matching strategy ("package match cascade", "priority cascade")
- Generic DB term (`.opencode/agents/spec-gap-analyzer.md`)

Flag any occurrences that were missed.

Additionally, grep for stale Markdown anchor fragments that pointed to
renamed section headings:

- `#recalculation-cascade` (now `#recalculation-chain`)
- `#cascade-execution-model` (now `#chain-execution-model`)
- `#execute_duplicate_cascade` (now `#execute_duplicate_flattening`)
- `#cascading-cleanup` (now `#orphan-cleanup`)
- `#cascading-composition` (now `#chain-composition`)

Expect zero matches — any remaining occurrences are broken links.

### Step 7: Resolve CVS-COH-08

Mark finding CVS-COH-08 in `docs/reviews/cvss-scoring.md` as RESOLVED
with compact format. Update `.tracking.json` cache and
`docs/reviews/README.md`.

### Step 8: Run reviewers

Launch `spec-coherence-reviewer` on all feature specs that were
modified (to verify no cross-reference was broken and no terminology
inconsistency remains):

- `cvss-scoring`
- `ticket-mutations`
- `cve-service`
- `ticket-service`
- `tickets`
- `package-model`
- `package-service`
- `product-lifecycle-transitions`
- `system-settings`
- `ad-integration`
- `user-service`

### Step 9: Delete this draft

Remove `docs/drafts/cascade-terminology-rename.md` — the plan has been
fully executed.

---

## Notes

### API surface change

The `"cascade"` → `"orphan_cleanup"` rename of the JSON response field
in soft-delete endpoints (`package-model.md`) is a specification-level
change only. No implementation code exists yet for these endpoints,
so this is not a breaking API change.
