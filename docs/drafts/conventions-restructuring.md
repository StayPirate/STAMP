# Conventions.md Restructuring Plan

## Objective

Reorganize `docs/conventions.md` (1312 lines) to improve navigability,
semantic coherence, and structural consistency — without modifying any
content semantics.

## Design Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | Ecosystem Naming goes under `## Terminology` | It is a terminological convention ("use OSSF canonical name, not GitHub's variant") |
| D2 | Runtime Version stays at the bottom of `## Python (Backend)` | It is Python-specific with Docker/CI implications; not worth a standalone `##` section |
| D3 | Redis sections merge under `### Redis` with `####` sub-headings | Closely related content that was arbitrarily split into siblings |
| D4 | CLI `### Naming` moves after `### Framework` | Semantically belongs with structure/design, not after Output Contract |
| D5 | "Feature Specifications" renames to "Specification Writing" | Clarifies this section is about *how to write specs*, not about features themselves |
| D6 | Heading names preserved wherever externally referenced (unless the rename clearly improves clarity AND references are few) | Minimizes churn |

## Target Structure

```
# Code Conventions

## Contents (TOC — top-level sections only)

## General Principles                              ← renamed from "## General"
  (5 bullet points only)

## Terminology                                     ← NEW section (extracted from General)
  ### External Identity / SSO Terminology
  ### Cascade / Chain / Flattening Terminology
  ### Ticket Status Category Terminology
  ### Ecosystem Naming

## Cross-Cutting Rules                             ← NEW section (extracted from General)
  ### Example Data in Documentation
  ### Username Format
  ### Timestamps & Timezones
  ### Configuration Management

## Python (Backend)                                ← same name, internal reorder
  ### Style
  ### Type Hints
  ### Naming
  ### FastAPI Conventions
  ### SQLAlchemy Conventions
  ### Enum Storage Strategy
  ### Pydantic Conventions
  ### Secret Field Typing
  ### Audit Trail
  ### Transaction and Locking
    #### Pessimistic Locking Pattern
    #### Transaction Hygiene Rules
  ### Redis                                        ← NEW parent (merge of 2 sections)
    #### Key Conventions                           ← renamed from "### Redis Key Conventions"
    #### Error Handling                            ← renamed from "### Redis Error Handling"
  ### Logging
  ### Testing Conventions
  ### Runtime Version                              ← moved to end of Python section
    #### Source of Truth
    #### Dockerfile Convention
    #### Version Bump Checklist
    #### Forward Compatibility (recommended, deferred)

## CLI Conventions                                 ← same name
  ### Framework
  ### Naming                                       ← MOVED from after Output Contract
  ### Command Design
  ### Database Access
  ### Output Contract
    #### Channel Separation
    #### Exit Codes
    #### Success Output
    #### Error Output
    #### Multi-Step Reporting (Fail-Fast)
    #### Idempotency
    #### Human-Readable Format
    #### Automated Verification

## Git Conventions                                 ← same name
  ### Branch Naming
  ### Commit Messages
  ### Versioning
    #### Version Source of Truth
    #### SemVer Interpretation
    #### Pre-1.0 Rules
    #### 1.0.0 Graduation Criteria
    #### Why Single Version

## Specification Writing                           ← renamed from "## Feature Specifications"
  ### API Cross-references
  ### Fetcher Documentation
  ### Function Specification Completeness
    #### Required Information (by function category)
    #### Structural freedom
    #### Consolidated groups
    #### Module-level defaults
    #### Decision rule
    #### Scope and Exclusions
    #### Algorithm-reference pattern
  ### Service Exception Conventions
    #### Base class requirement
    #### API-facing exception table format
    #### System-internal exception sub-table
    #### "Raised when" column rules
    #### 1:1 mapping rule
    #### Domain-specific validation codes
    #### Mapping authority chain
    #### Shared exceptions
    #### Endpoint error tables (post-standardization)
```

## Phases

### Phase 0 — Baseline Snapshot

**Goal**: capture the current state as a verification checklist.

**Actions**:
1. Record total line count of `docs/conventions.md`
2. Record complete heading tree with line numbers
3. Record all cross-references from other files that point to
   conventions.md sections (file, line, referenced section name)

**Commit**: none (data captured in this draft)

**Verification**: baseline data stored below in "Phase 0 Data" section.

---

### Phase 1 — Restructure `docs/conventions.md`

**Goal**: produce the target structure in a single atomic operation.

**Actions**:
1. Rewrite `docs/conventions.md` with the target structure
2. Add TOC at the top (top-level `##` sections only)
3. Move sections from "General" into "Terminology" and "Cross-Cutting
   Rules"
4. Merge Redis Key Conventions + Redis Error Handling under `### Redis`
5. Move CLI `### Naming` after `### Framework`
6. Move `### Runtime Version` to end of Python section
7. Rename `## General` → `## General Principles`
8. Rename `## Feature Specifications` → `## Specification Writing`

**Commit**: `docs: restructure conventions.md layout`

**Verification**:
1. Extract non-heading lines from old and new file, sort, compare —
   any missing line = content loss
2. Verify line count: new file = old file + TOC lines added
3. Verify all original `###` and `####` headings still exist (with
   expected renames)

---

### Phase 2 — Update Cross-References

**Goal**: update all files that reference renamed/moved sections.

**Known references to update**:

| Old reference text | New reference text | Files |
|---|---|---|
| `(Redis Error Handling)` | `(Redis, Error Handling)` | `docs/deployment.md`, `docs/features/platform/cli-infrastructure.md` |
| `"Feature Specifications > API Cross-references"` | `"Specification Writing > API Cross-references"` | `docs/reviews/user-management.md` |

**Actions**:
1. Update the references listed above
2. Grep for any other parenthetical references to "Redis Key
   Conventions" or "Redis Error Handling" that were missed in analysis
3. Grep for any reference to "Feature Specifications" pointing to
   conventions.md

**Commit**: `docs: update cross-references to conventions.md`

**Verification**:
1. Grep `conventions.md.*Redis Key Conventions` — expect 0 results
2. Grep `conventions.md.*Redis Error Handling` — expect 0 results
3. Grep `conventions.md.*Feature Specifications` — expect 0 results
4. All existing parenthetical references still resolve to a valid
   heading in the restructured file

---

### Phase 3 — Anchor Link Audit

**Goal**: verify no broken anchor links exist project-wide.

**Actions**:
1. Extract all markdown anchor links (`[text](#anchor)` and
   `[text](path#anchor)`) across `docs/` and `.opencode/`
2. For links pointing to `conventions.md`, verify each anchor resolves
   to a heading in the new structure
3. For links pointing to other files that reference conventions.md
   sections in prose (parenthetical style), verify the section name
   matches a heading
4. Fix any remaining broken references

**Commit**: `docs: fix remaining anchor link references` (if fixes
needed; skip if clean)

**Verification**:
1. Re-run the anchor extraction and confirm 0 broken links to
   conventions.md
2. Grep for old heading names that no longer exist — expect 0 results

---

### Phase 4 — Run Reviewers

**Goal**: verify the restructuring introduced no coherence or placement
issues.

**Actions**:
1. Run `@docs-placement-reviewer` on `docs/conventions.md` — verify
   that the new section groupings (Terminology, Cross-Cutting Rules)
   make sense from a placement perspective
2. Run `@spec-coherence-reviewer` on `docs/conventions.md` — verify
   no contradictions were introduced by the reorganization

**Commit**: fix any issues found by reviewers (if needed)

**Verification**: reviewer reports show no "Needs revision" findings.

---

### Phase 5 — Delete Draft

**Goal**: remove this planning document.

**Actions**:
1. Delete `docs/drafts/conventions-restructuring.md`

**Commit**: `docs: remove conventions restructuring draft`

---

## Phase 0 Data

### Total Line Count

```
1312 lines
```

### Heading Tree (with line numbers)

```
L1:    # Code Conventions
L3:    ## General
L19:     ### Example Data in Documentation
L42:     ### Username Format
L51:     ### External Identity / SSO Terminology
L68:     ### Cascade / Chain / Flattening Terminology
L89:     ### Ticket Status Category Terminology
L126:    ### Ecosystem Naming
L153:    ### Timestamps & Timezones
L183:    ### Configuration Management
L220:  ## Python (Backend)
L222:    ### Style
L234:    ### Type Hints
L239:    ### Naming
L260:    ### FastAPI Conventions
L323:    ### SQLAlchemy Conventions
L357:    ### Enum Storage Strategy
L417:    ### Pydantic Conventions
L423:    ### Secret Field Typing
L466:    ### Audit Trail
L477:    ### Transaction and Locking
L484:      #### Pessimistic Locking Pattern
L500:      #### Transaction Hygiene Rules
L534:    ### Testing Conventions
L552:    ### Redis Key Conventions
L573:    ### Redis Error Handling
L619:    ### Logging
L639:    ### Runtime Version
L649:      #### Source of Truth
L672:      #### Dockerfile Convention
L688:      #### Version Bump Checklist
L722:      #### Forward Compatibility (recommended, deferred)
L736:  ## CLI Conventions
L742:    ### Framework
L749:    ### Command Design
L764:    ### Database Access
L772:    ### Output Contract
L777:      #### Channel Separation
L785:      #### Exit Codes
L797:      #### Success Output
L806:      #### Error Output
L822:      #### Multi-Step Reporting (Fail-Fast)
L848:      #### Idempotency
L869:      #### Human-Readable Format
L880:      #### Automated Verification
L897:    ### Naming
L902:  ## Git Conventions
L904:    ### Branch Naming
L911:    ### Commit Messages
L923:    ### Versioning
L930:      #### Version Source of Truth
L939:      #### SemVer Interpretation
L954:      #### Pre-1.0 Rules
L963:      #### 1.0.0 Graduation Criteria
L986:      #### Why Single Version
L995:  ## Feature Specifications
L997:    ### API Cross-references
L1005:   ### Fetcher Documentation
L1014:   ### Function Specification Completeness
L1022:     #### Required Information (by function category)
L1061:     #### Structural freedom
L1084:     #### Consolidated groups
L1092:     #### Module-level defaults
L1117:     #### Decision rule
L1147:     #### Scope and Exclusions
L1195:     #### Algorithm-reference pattern
L1204:   ### Service Exception Conventions
L1210:     #### Base class requirement
L1222:     #### API-facing exception table format
L1230:     #### System-internal exception sub-table
L1239:     #### "Raised when" column rules
L1248:     #### 1:1 mapping rule
L1255:     #### Domain-specific validation codes
L1265:     #### Mapping authority chain
L1283:     #### Shared exceptions
L1303:     #### Endpoint error tables (post-standardization)
```

### Cross-Reference Inventory

All files referencing `docs/conventions.md` with a section name:

**"Example Data in Documentation"**:
- `AGENTS.md:803` — `docs/conventions.md`, "Example Data in Documentation"`
- `docs/features/platform/logging.md:344` — `docs/conventions.md` ("Example Data in Documentation")

**"Username Format"**:
- `docs/features/identity/user-management.md:73` — `docs/conventions.md`, Username Format

**"External Identity / SSO Terminology"**:
- `.opencode/prompts/spec.md:92` — `docs/conventions.md` (External Identity/SSO, ...)

**"Ecosystem Naming"**:
- `docs/data-model.md:691` — `docs/conventions.md` (Ecosystem Naming)
- `docs/features/tickets/cve-sync-ghsa.md:391` — `docs/conventions.md`, Ecosystem Naming
- `docs/features/tickets/cve-sync-osv.md:262` — `conventions.md`, Ecosystem Naming

**"Timestamps & Timezones"**:
- `docs/api-spec.md:187` — `docs/conventions.md` (Timestamps & Timezones)
- `docs/data-model.md:1699` — `docs/conventions.md` (Timestamps & Timezones)
- `docs/features/platform/fetcher-infrastructure.md:1483` — `docs/conventions.md` (Timestamps ...)
- `docs/features/platform/git-fetcher-infrastructure.md:661` — `docs/conventions.md`, Timestamps & Timezones
- `docs/features/platform/logging.md:131` — `docs/conventions.md`
- `docs/features/platform/logging.md:436` — `docs/conventions.md` (Timestamps & Timezones, ...)

**"Enum Storage Strategy"**:
- `docs/data-model.md:1693` — `docs/conventions.md` (Enum Storage Strategy)
- `docs/drafts/open-points.md:466` — `docs/conventions.md` (Enum Storage Strategy)

**"FastAPI Conventions"**:
- `docs/api-spec.md:688` — `docs/conventions.md` (FastAPI Conventions)
- `docs/features/identity/rbac.md:732` — `docs/conventions.md` — FastAPI ...
- `.opencode/skills/new-api-endpoint/SKILL.md:75` — `docs/conventions.md`, FastAPI Conventions

**"SQLAlchemy Conventions"**:
- `docs/features/platform/cli-infrastructure.md:111` — `docs/conventions.md`, SQLAlchemy Conventions
- `docs/features/platform/fetcher-infrastructure.md:1652` — `docs/conventions.md`, SQLAlchemy Conventions

**"Pydantic Conventions"**:
- `.opencode/skills/new-api-endpoint/SKILL.md:48` — `docs/conventions.md`, Pydantic Conventions

**"Secret Field Typing"**:
- `docs/features/platform/logging.md:341` — `docs/conventions.md`, Secret Field Typing
- `docs/features/platform/logging.md:436` — ... Secret Field Typing, ...

**"Transaction and Locking"** / **"Transaction Hygiene Rules"**:
- `docs/features/packages/package-service.md:90` — `docs/conventions.md` (Transaction Hygiene Rules)
- `docs/features/packages/package-service.md:848` — `docs/conventions.md` (Transaction and Locking)
- `docs/features/packages/package-service.md:976` — `docs/conventions.md` — Transaction and Locking
- `.opencode/agents/ticket-integrity-reviewer.md:157` — `docs/conventions.md` (Transaction and Locking)
- `.opencode/agents/ticket-integrity-reviewer.md:173` — `docs/conventions.md` (Transaction and Locking)

**"Testing Conventions"**:
- `docs/features/platform/testing-strategy.md:7` — `docs/conventions.md` (Testing Conventions)
- `docs/features/platform/testing-strategy.md:682` — `docs/conventions.md` — Testing Conventions

**"Redis Key Conventions"**:
- (no explicit parenthetical references found)

**"Redis Error Handling"**:
- `docs/deployment.md:592` — `docs/conventions.md`, Redis Error Handling
- `docs/features/platform/cli-infrastructure.md:282` — `docs/conventions.md`, Redis Error Handling

**"CLI Conventions"**:
- `docs/cli-reference.md:4` — `docs/conventions.md` (CLI Conventions)
- `docs/features/identity/user-management.md:36` — `docs/conventions.md` (CLI Conventions)
- `docs/features/platform/cli-infrastructure.md:12` — `docs/conventions.md` (CLI Conventions)
- `docs/features/platform/cli-infrastructure.md:39` — `docs/conventions.md` (CLI Conventions)
- `docs/features/platform/cli-infrastructure.md:253` — `docs/conventions.md` (CLI Conventions)
- `docs/features/platform/cli-infrastructure.md:278` — `docs/conventions.md` (CLI Conventions)
- `docs/features/platform/cli-infrastructure.md:282` — `docs/conventions.md` (CLI Conventions)
- `docs/features/platform/cli-infrastructure.md:283` — `docs/conventions.md` (CLI Conventions)
- `docs/features/platform/cli-infrastructure.md:309` — `docs/conventions.md` (CLI Conventions)
- `docs/features/platform/cli-infrastructure.md:429` — `docs/conventions.md` (CLI Conventions)
- `docs/features/platform/testing-strategy.md:625` — `docs/conventions.md` (CLI Conventions, ...)
- `docs/features/platform/testing-strategy.md:682` — ... CLI Conventions ...
- `docs/features/platform/fetcher-operations.md:966` — `docs/conventions.md` for CLI conventions

**"Logging"**:
- (references point to `docs/features/platform/logging.md`, not to
  conventions.md Logging section)

**"Runtime Version"** / **"Python Runtime"**:
- `docs/features/platform/cli-infrastructure.md:81` — `docs/conventions.md` (Runtime Version)
- `docs/features/platform/git-fetcher-infrastructure.md:379` — `docs/conventions.md`, Python Runtime

**"Audit Trail"**:
- `docs/features/identity/identity-audit-log.md:294` — `docs/conventions.md` — Audit Trail Conventions
- `docs/features/platform/audit-trail-infrastructure.md:362` — `docs/conventions.md` — Audit Trail ...

**"Sync-to-Async"** (inside SQLAlchemy Conventions):
- `docs/features/platform/cli-infrastructure.md:178` — `docs/conventions.md` ...
- `docs/features/platform/cli-infrastructure.md:218` — sync-to-async bridging convention (`docs/conventions.md`, ...)
- `docs/features/platform/testing-strategy.md:485` — `docs/conventions.md`, Sync-to-Async ...
- `docs/features/platform/fetcher-infrastructure.md:2689` — sync-to-async bridging pattern (`docs/conventions.md`)

**"Command Design"**:
- `docs/features/platform/cli-infrastructure.md:330` — `docs/conventions.md` (Command Design)

**"Human-Readable Format"**:
- `docs/features/platform/cli-infrastructure.md:31` — `docs/conventions.md` (Human-Readable Format)

**"Multi-Step Reporting"**:
- (referenced in reviews/user-management.md as inline mention)

**"Automated Verification"**:
- `docs/features/platform/cli-infrastructure.md:429` — ... `docs/conventions.md` (CLI Conventions) ...

**"Service Exception Conventions"**:
- `docs/features/platform/cli-infrastructure.md:280` — `docs/conventions.md`, Service Exception Conventions
- `.opencode/skills/new-api-endpoint/SKILL.md:57` — `docs/conventions.md` ...

**"Feature Specifications"** (section name):
- `docs/reviews/user-management.md:13` — conventions.md (Feature Specifications > API Cross-references)

**"Git Conventions"**:
- `docs/deployment.md:339` — `docs/conventions.md` (Git Conventions)

**"Function Specification Completeness"**:
- `.opencode/agents/spec-gap-analyzer.md:188` — `docs/conventions.md` ...
- `docs/features/platform/cve-record-parser.md:65` — Category B functions per `docs/conventions.md`

### Impact Summary

| Heading rename | Cross-references to update | Count |
|---|---|---|
| `## General` → `## General Principles` | 0 | 0 |
| `## Feature Specifications` → `## Specification Writing` | `docs/reviews/user-management.md` | 1 |
| `### Redis Key Conventions` → `#### Key Conventions` | (none) | 0 |
| `### Redis Error Handling` → `#### Error Handling` | `docs/deployment.md`, `docs/features/platform/cli-infrastructure.md` | 2 |

All other headings preserve their exact names — only their position in
the document changes. Since cross-references use parenthetical style
(`(Section Name)`) rather than anchor links (`#section-name`), moves
without renames do not break any references.
