# Draft: Restructure api-spec.md

## Objective

Reorganize the hierarchical structure of `docs/api-spec.md` to improve
navigability and logical grouping. The current file has a single
monolithic `## General Conventions` H2 section containing ~773 lines
with ~20 H3 children. Related concepts (response format, error handling,
global/scoped responses) are scattered with unrelated sections in
between.

**Constraint**: this is a purely structural change. No content is added,
removed, or semantically modified. Section text remains identical; only
the heading hierarchy and section ordering change.

## Current Structure

```
# API Specification
  ## General Conventions                          ← monolithic (773 lines)
    ### Base URL
    ### Authentication
    ### Authorization
      #### Authorization Chain Evaluation Order
    ### Response Format
      #### Error Code Categories
      #### Infrastructure Dependency Errors (HTTP 503)
    ### Pagination
    ### Filtering
      #### Query Parameter Length Limit
      #### Enum Filter Validation
      #### Date Range Interpretation
    ### Sorting
      #### Semantic Sort Fields
      #### Sort Parameter Validation
    ### Request Tracing
    ### Rate Limiting
    ### Global Responses
      #### What belongs in an endpoint error table
    ### Scoped Responses
      #### Ticket Accessibility Check
      #### CVE Accessibility Check
      #### Manual-Zone Mutability Guard
    ### Response Applicability Derivation
      #### Global Response Derivation
      #### Scoped Response Derivation
      #### Genuine Exceptions
    ### Versioning
    ### User Identifier Resolution
      #### User References in Responses
    ### CVE Identifier Resolution
    ### Mutation Patterns
    ### Partial Update Semantics
    ### Audit Trail Endpoint Naming
  ## Endpoint Index
```

## Target Structure

```
# API Specification

## Fundamentals
  ### Base URL
  ### Versioning                                  ← moved from after Scoped Responses

## Authentication and Authorization
  ### Authentication
  ### Authorization
    #### Authorization Chain Evaluation Order

## Request Conventions
  ### Pagination
  ### Filtering
    #### Enum Filter Validation
    #### Date Range Interpretation
  ### Query Parameter Length Limit              ← promoted from under Filtering (applies to all string params)
  ### Sorting
    #### Semantic Sort Fields
    #### Sort Parameter Validation
  ### Request Tracing
  ### Rate Limiting

## Response Conventions
  ### Response Format
    #### Error Code Categories
    #### Infrastructure Dependency Errors (HTTP 503)
  ### Global Responses
    #### What belongs in an endpoint error table
  ### Scoped Responses
    #### Ticket Accessibility Check
    #### CVE Accessibility Check
    #### Manual-Zone Mutability Guard
  ### Response Applicability Derivation
    #### Global Response Derivation
    #### Scoped Response Derivation
    #### Genuine Exceptions

## Identifier Resolution
  ### User Identifier Resolution
    #### User References in Responses
  ### CVE Identifier Resolution

## Mutation Conventions
  ### Mutation Patterns
  ### Partial Update Semantics

## Naming Conventions
  ### Audit Trail Endpoint Naming

## Endpoint Index
```

## Anchor Impact Analysis

Markdown anchors are derived from heading text, not heading level. Since
no existing heading is renamed (only moved or wrapped under new H2
parents), all existing anchors survive:

- `#base-url`, `#authentication`, `#authorization`, `#response-format`,
  `#pagination`, `#filtering`, `#sorting`, `#versioning`,
  `#query-parameter-length-limit`, etc. — all unchanged

**Only breaking anchor**: `#general-conventions` (the H2 container is
removed entirely).

**Level change (anchor preserved)**: `#### Query Parameter Length Limit`
is promoted to `### Query Parameter Length Limit`. The anchor
`#query-parameter-length-limit` is unchanged (derived from text, not
level).

**Self-reference affected**: the prescriptive template in Semantic Sort
Fields references "General Conventions" by name (not as a link anchor,
but as prose text that spec authors copy). This must be updated to
reference the correct post-restructuring section name ("Sorting").

**New anchors created**: `#fundamentals`, `#authentication-and-authorization`,
`#request-conventions`, `#response-conventions`, `#identifier-resolution`,
`#mutation-conventions`, `#naming-conventions`.

## Execution Phases

### Phase 1 — Restructure `api-spec.md`

**Scope**: rewrite the file with the target structure. No semantic
content changes — only heading levels and section ordering.

**Note**: internal self-references within `api-spec.md` that point to
the removed `## General Conventions` heading are updated in Phase 2
(not here), because they are reference-fix work, not structural work.

**Verification**:
1. Commit the change
2. Diff against previous commit
3. Verify no content was lost or added (only structural markers changed)
4. If content is missing, fix and commit again

### Phase 2 — Fix broken anchor links

**Scope**: search the entire project — including `api-spec.md` itself —
for references to `api-spec.md#general-conventions` (the only breaking
anchor), internal self-references to the removed section name, and any
other textual references to "General Conventions section" that need
updating.

Known self-reference in `api-spec.md`:
- Semantic Sort Fields (line ~298-300): the prescriptive template
  `"semantic ordering (see General Conventions)"` must be updated to
  `"semantic ordering (see Sorting)"` — the target section `### Sorting`
  remains as a valid heading under `## Request Conventions`

Known external references:
- `docs/features/platform/health-endpoints.md` (line 191-192): reference
  to "General Conventions" must be updated to "Response Conventions" —
  the cross-reference explains why health endpoints deviate from the
  standard response envelope format

Files to audit:
- `docs/api-spec.md` (self-references)
- `AGENTS.md`
- `docs/conventions.md`
- `docs/features/**/*.md`
- `.opencode/**/*.md`

**Verification**:
1. Commit the fixes
2. Diff against previous commit
3. Verify all broken anchors are fixed and no unrelated changes crept in

### Phase 3 — Update AGENTS.md prose references

**Scope**: if `AGENTS.md` or other project-level files reference the old
structure by name (e.g., "the General Conventions section of
api-spec.md"), update the prose to reference the correct new section.

**Verification**:
1. Commit the changes
2. Diff against previous commit
3. Verify only the necessary references were updated

### Phase 4 — Run reviewers

**Scope**: invoke the following reviewers on the restructured
`api-spec.md` and any modified specs:

- `@spec-coherence-reviewer` — verify no contradictions introduced
  between `api-spec.md` and feature specs that reference it
- `@docs-reviewer` — verify documentation completeness and coherence
- `@docs-placement-reviewer` — verify information placement is correct
  after the reorganization

If reviewers identify issues, fix them and commit.

### Phase 5 — Delete this draft

**Scope**: remove `docs/drafts/restructure-api-spec.md` and commit.

## Risks

| Risk | Mitigation |
|------|------------|
| Anchor `#general-conventions` referenced elsewhere | Phase 2 performs exhaustive search |
| Content accidentally lost during rewrite | Phase 1 diff check verifies byte-level preservation |
| Feature specs that say "see General Conventions" in prose | Phase 2/3 catch textual references |
| Reviewer finds placement issue post-move | Phase 4 catches before merge |

## Decision Log

- **Atomic restructuring**: the file rewrite is done in one commit (Phase
  1) because intermediate states would leave the document in an
  inconsistent structure
- **No content changes**: strictly enforced — if a sentence needs
  rewording to fit the new structure, it is out of scope for this draft
  and should be proposed separately
- **Branch**: work on a dedicated branch (`docs/restructure-api-spec`)
