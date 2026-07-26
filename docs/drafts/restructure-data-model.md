# Restructure data-model.md

## Objective

Improve the structural quality, consistency, and navigability of
`docs/data-model.md` without changing the semantic content of the data
model itself. The file currently has 35 entity definitions across 1570
lines with structural inconsistencies that hinder readability.

## Principles

- **No information loss**: every piece of information in the current file
  must be preserved — either in `data-model.md` itself or relocated to its
  owning feature spec (with a cross-reference left behind)
- **No semantic changes**: table definitions, constraints, column types,
  and relationships must remain identical
- **Incremental commits**: one commit per phase, with diff verification
  after each to ensure nothing was lost
- **Spec-only scope**: no implementation code, migrations, or tests are
  affected

## Phases

---

### Phase 1: Naming consistency between overview and ER sections

**Scope**: Fix naming mismatches between the overview flowchart subgraph
labels and the ER diagram section headings.

**Actions**:

| Current (overview flowchart) | Current (ER section heading) | Target (unified) |
|------------------------------|------------------------------|-------------------|
| `CVE & Tickets` | `CVE & Ticket Core` | `CVE & Ticket Core` |
| `Platform` | `Platform Infrastructure` | `Platform Infrastructure` |

- Update the `subgraph` labels in the overview Mermaid flowchart to match
  the ER section headings
- Verify no other naming inconsistencies exist between the two levels

**Verification criteria**:

- `git diff` shows only the two subgraph label changes in the overview
  flowchart (lines 22, 52 area)
- No content lines (table definitions, column descriptions) are modified
- Ctrl+F for the old labels returns zero matches

---

### Phase 2: Fix overview flowchart — add CVECVSSAssessment

**Scope**: Add `CVECVSSAssessment` to the overview flowchart. It is the
most business-critical enrichment entity (drives severity resolution,
eligibility thresholds) and its absence from the overview is a gap.

**Actions**:

- Add `CVECVSSAssessment` to the `cve_enrichment` subgraph in the
  overview flowchart
- Add the relationship arrow `CVE --> CVECVSSAssessment`
- Consider whether `CVESource` and `TicketReference` should also be
  added. Decision criteria: if they appear in domain ER diagrams AND
  have cross-domain relationships, they belong in the overview.
  `CVESource` has no cross-domain FK — skip. `TicketReference` has no
  cross-domain FK — skip. `CVECVSSAssessment` drives eligibility
  (cross-domain with Package Model via CVSS threshold) — include.

**Verification criteria**:

- `git diff` shows additions only in the overview flowchart block
  (no deletions of existing entities or arrows)
- The entity count in the introductory text updates from the current
  overview count if a count is mentioned
- All existing arrows remain unchanged

---

### Phase 3: Reorganize tables by domain

**Scope**: Reorder the flat list of H3 table headings in the `## Tables`
section to follow the domain grouping established by the ER diagrams.
Add domain separator headings for navigability.

**Target structure**:

```
## Tables

### CVE & Ticket Core
#### CVE
#### CVESource
#### CVESourceFetchStatus Enum
#### CVESourceType Python Enum
#### CVECVSSAssessment
#### CVEExternalIdentifierSource Python Enum
#### CVEExternalIdentifier
#### CVEAffectedVersion
#### CVECWE
#### CVESSVCAssessment
#### CVEKEVEntry
#### CVEEPSSScore
#### Ticket
#### TicketReference
#### ReferenceType Enum
#### TicketAuditEvent
#### TicketAuditEventType Enum
#### TicketAccessGrant

### Package Model
#### TicketPackage
#### TicketPackageTrack
#### TicketPackageProduct
#### PackageStatus Enum
#### DeliveryStatus Enum
#### WorkflowType Enum
#### Product
#### ProductRepository

### Identity
#### User
#### UserRole
#### RoleMapping
#### Session
#### ApiKey
#### IdentityAuditEvent
#### IdentityAuditEventType Enum

### Platform Infrastructure
#### SystemSetting
#### SettingAuditEvent
#### SettingAuditEventType Enum
#### FetcherConfig
#### FetcherRun
#### FetcherAuditEvent

### IBS Integration
#### CodestreamPackageChecksum
#### PackageBugowner
#### PackageBugownerMember
#### SubmissionRequest
#### SubmissionRequestTrack
#### ReleaseRequest
```

**Actions**:

- Promote `## Tables` to remain H2
- Add H3 domain headings: `CVE & Ticket Core`, `Package Model`,
  `Identity`, `Platform Infrastructure`, `IBS Integration`
- Demote current H3 table/enum headings to H4
- Reorder sections to match the target structure above
- Move `Ticket` from its current position (after ApiKey) to the CVE &
  Ticket Core group
- Move `SystemSetting` from its current position (after CVEEPSSScore)
  to the Platform Infrastructure group

**Verification criteria**:

- `git diff --stat` shows only `data-model.md` modified
- Line count comparison: the total non-blank content lines must remain
  identical (reordering does not add/remove content)
- Every H3/H4 heading that existed before still exists (possibly at a
  different heading level)
- Run a word-count comparison on the file before and after: character
  count should differ only by the added domain heading lines
- Specifically verify: all unique constraint blocks, all CHECK constraint
  blocks, all index blocks, and the Notes section remain intact and
  associated with the correct table

---

### Phase 4: Separate AuditEventMixin into a dedicated section

**Scope**: Move `AuditEventMixin` out of the flat table listing into its
own clearly-labeled section that communicates it is a shared mixin, not a
physical database table.

**Actions**:

- Create a new H3 section (within `## Tables`) titled
  `### Shared Structures` immediately before the first domain group
- Move the `AuditEventMixin` content (current H4 after Phase 3) into this
  section as `#### AuditEventMixin`
- Add a one-line note: "This section documents shared SQLAlchemy
  structures that are not physical database tables."

**Verification criteria**:

- `git diff` shows the AuditEventMixin block moved, not deleted
- The content of the mixin section is byte-for-byte identical to the
  original (only heading level and position change)
- The three audit event tables (TicketAuditEvent, IdentityAuditEvent,
  SettingAuditEvent, FetcherAuditEvent) still reference the mixin via
  their "Inherits..." text

---

### Phase 5: Standardize enum documentation pattern

**Scope**: Ensure all enums follow a consistent documentation pattern
with explicit Category classification.

**Target pattern for each enum**:

```markdown
#### EnumName Enum

<Brief description of what it enumerates>. Category <A|B> —
<"state-machine (VARCHAR + CHECK)" | "classification (Python Enum only)">.
<Adding-a-value instructions>.

| Value | Description |
|-------|-------------|
| ...   | ...         |
```

**Actions**:

1. For enums that currently have their own heading but lack Category
   classification, add the Category line:
   - `PackageStatus Enum` → add "Category A — state-machine (VARCHAR +
     CHECK constraint `chk_ticket_package_track_status_valid`)"
   - `DeliveryStatus Enum` → add "Category A — state-machine (VARCHAR +
     CHECK constraint `chk_ticket_package_track_delivery_status_valid`)"
   - `WorkflowType Enum` → determine category (likely A — state-machine
     for track type). Add classification
   - `ReferenceType Enum` → determine category (likely B — classification).
     Add classification
   - `TicketAuditEventType Enum` → add "Category B — classification
     (Python Enum only)"
   - `IdentityAuditEventType Enum` → add "Category B — classification
     (Python Enum only)"
   - `SettingAuditEventType Enum` → add "Category B — classification
     (Python Enum only)"

2. For enums currently documented inline (no own heading), extract into
   a dedicated H4 section immediately after their owning table:
   - `SubmissionRequestState` (currently inline text after
     SubmissionRequest table) → extract to
     `#### SubmissionRequestState Enum`
   - `ReleaseRequestState` (currently inline text after ReleaseRequest
     table) → extract to `#### ReleaseRequestState Enum`
   - `BugownerType` (currently bold+table within PackageBugowner) →
     extract to `#### BugownerType Enum`
   - `Role` enum values (currently within UserRole section) → extract to
     `#### Role Enum`
   - `FetcherRunStatus` (currently inline in FetcherRun description) →
     extract to `#### FetcherRunStatus Enum` (if not already standalone)
   - `FetcherRunTriggeredBy` (currently inline) → extract to
     `#### FetcherRunTriggeredBy Enum`
   - `FetcherAuditEventType` (currently inline in FetcherAuditEvent) →
     extract to `#### FetcherAuditEventType Enum`

3. Update the Notes section enum list to reflect any newly-extracted
   enums and verify the Category A/B classification is consistent

**Verification criteria**:

- Every enum value that existed before still exists in the file
- No enum values were changed, added, or removed
- The Notes section's enum classification list is consistent with the
  per-enum Category declarations
- Each enum section follows the standardized pattern

---

### Phase 6: Relocate operational details to owning specs

**Scope**: Identify content in `data-model.md` that exceeds the
responsibility of a schema definition (operational policies, UI notes,
detailed mechanism descriptions) and either remove it (if already present
in the owning spec) or move it there (if not present elsewhere).

**Identified items**:

| Item | Location in data-model.md | Owning spec | Action |
|------|---------------------------|-------------|--------|
| UI display note for EPSS (staleness indicator, frontend SHOULD display) | Lines 758-763 (CVEEPSSScore) | `docs/features/tickets/cve-sync-epss.md` | Move to owning spec (append after line 117 area). Leave a short reference in data-model.md: "See `cve-sync-epss.md` for display guidance" |
| Session cleanup policy detail (weekly task, criteria, retention) | Lines 1043-1047 (Session) | `docs/features/identity/authentication.md` (lines 285-309) | Remove from data-model.md — already present in full detail in the owning spec. Replace with: "See `authentication.md` (Session cleanup) for retention policy" |
| CVESource `first_failed_at` detailed retry mechanism explanation | Line 458 (CVESource table, `first_failed_at` column Description) | `docs/features/platform/cve-source-failure-retry.md` | Reduce to column-level semantics: "Timestamp when the current failure streak began. NULL when status is not `failure`. See `cve-source-failure-retry.md` for the full retry mechanism" |

**Actions**:

1. For each item, verify the owning spec contains the information (or
   move it there if not)
2. Replace verbose operational text in `data-model.md` with a concise
   column-level description + cross-reference
3. Ensure the EPSS UI display note is preserved in the destination spec

**Verification criteria**:

- No information was deleted without being present elsewhere
- For the EPSS UI note: verify it now appears in `cve-sync-epss.md`
- For the Session cleanup: verify `authentication.md` already contains
  the identical information (it does — lines 285-309)
- For the CVESource retry: verify `cve-source-failure-retry.md` already
  contains the identical information (it does — lines 39-57, 82-84,
  288-290)
- `data-model.md` column descriptions remain sufficient to understand the
  column's purpose and type without consulting the feature spec
- Cross-references use the standard format:
  `See \`docs/features/.../spec.md\` for details`

---

### Phase 7: Add Table of Contents

**Scope**: Add a navigable TOC after the introductory paragraph,
organized by domain, linking to all major sections.

**Actions**:

- Insert a TOC section between the introduction (line 4) and
  `## Entity Relationship Overview` (line 6)
- Use markdown anchor links to all H2 and H3 headings
- Organize TOC entries by domain (matching the reorganized structure
  from Phase 3)
- Keep the TOC concise: list domain groups and tables, not individual
  columns or sub-sections

**Target format**:

```markdown
## Contents

- [Entity Relationship Overview](#entity-relationship-overview)
  - [Overview](#overview)
  - [CVE & Ticket Core](#cve--ticket-core)
  - [Package Model](#package-model)
  - [Identity](#identity)
  - [Platform Infrastructure](#platform-infrastructure)
  - [IBS Integration](#ibs-integration)
- [Tables](#tables)
  - [Shared Structures](#shared-structures)
  - [CVE & Ticket Core](#cve--ticket-core-1)
  - [Package Model](#package-model-1)
  - [Identity](#identity-1)
  - [Platform Infrastructure](#platform-infrastructure-1)
  - [IBS Integration](#ibs-integration-1)
- [Notes](#notes)
```

**Verification criteria**:

- Every anchor link in the TOC resolves to an existing heading in the
  document
- The TOC reflects the final structure after Phases 1-6
- No content from the document body was modified (only an insertion)

---

## Final Steps

### Step 8: Run reviewers

Execute the following reviewers on the modified files:

1. **`@data-model-reviewer`** on `docs/data-model.md` — verify the
   restructured document still follows data model conventions
2. **`@spec-coherence-reviewer`** on `docs/data-model.md` — verify no
   contradictions were introduced with feature specs that reference the
   data model
3. **`@docs-reviewer`** on `docs/data-model.md` — verify documentation
   completeness and coherence with implementation specs

If any reviewer identifies issues rated "Needs revision", fix them before
proceeding to Step 9.

Additionally, if Phase 6 modified `docs/features/tickets/cve-sync-epss.md`:

4. **`@docs-placement-reviewer`** on `cve-sync-epss.md` — verify the
   relocated content is appropriately placed

### Step 9: Delete this draft

Once all phases are complete and reviewers pass:

```
rm docs/drafts/restructure-data-model.md
```

---

## Risk Assessment

| Phase | Risk level | Primary risk |
|-------|------------|--------------|
| 1 | Low | Typo in subgraph label |
| 2 | Low | Incorrect Mermaid syntax |
| 3 | **High** | Sections accidentally truncated or misplaced during reorder |
| 4 | Low | Broken cross-references from audit tables |
| 5 | Medium | Enum values accidentally changed during reformatting |
| 6 | Medium | Information removed that doesn't exist in destination |
| 7 | Low | Broken anchor links |

Phase 3 is the highest-risk operation. Extra verification steps:

- Before: count total lines, count unique constraint blocks, count CHECK
  constraint blocks, count index blocks
- After: verify all counts match
- Perform a sorted-lines comparison to detect any accidental deletions
