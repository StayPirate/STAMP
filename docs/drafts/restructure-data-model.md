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

**Target structure** (shows the final state after all phases; Phase 3
operates only on headings that already exist — entries suffixed with
`†` will be created by Phase 5b):

```
## Tables

### CVE & Ticket Core
#### CVE
#### CveState Enum †
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
#### TicketStatus Enum †
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
#### Role Enum †
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
#### FetcherRunStatus Enum †
#### FetcherRunTriggeredBy Enum †
#### FetcherAuditEvent
#### FetcherAuditEventType Enum †

### IBS Integration
#### CodestreamPackageChecksum
#### PackageBugowner
#### BugownerType Enum †
#### PackageBugownerMember
#### SubmissionRequest
#### SubmissionRequestState Enum †
#### SubmissionRequestTrack
#### ReleaseRequest
#### ReleaseRequestState Enum †
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
- Heading inventory: all original H3 headings are now H4 under their
  domain group; 5 new H3 domain headings were added; no heading was
  deleted
- Body content preservation: extract all non-heading lines from before
  and after; diff must show zero differences (reordering does not
  add/remove/modify content lines)
- Per-section association check: extract content sections by H4 heading
  (from heading to next heading). Compare each extracted section's body
  against the pre-reorder version keyed by the same heading text.
  Differences indicate misassociation (a block landed under the wrong
  heading during reorder)

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
- The four audit event tables (TicketAuditEvent, IdentityAuditEvent,
  SettingAuditEvent, FetcherAuditEvent) still reference the mixin via
  their "Inherits..." text

---

### Phase 5a: Add Category classification to existing enum headings

**Scope**: Add explicit Category A/B classification to the 7 standalone
enum sections that currently lack it. This is an append-only operation —
no content is moved or restructured.

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

- For enums that currently have their own heading but lack Category
  classification, add the Category line:
   - `PackageStatus Enum` → add "Category A — state-machine (VARCHAR +
     CHECK constraint `chk_ticket_package_track_status_valid`)"
   - `DeliveryStatus Enum` → add "Category A — state-machine (VARCHAR +
     CHECK constraint `chk_ticket_package_track_delivery_status_valid`)"
   - `WorkflowType Enum` → add "Category B — classification (Python
     Enum only)". WorkflowType is a static classification assigned once
     at track creation, not a state that transitions
   - `ReferenceType Enum` → add "Category B — classification (Python
     Enum only)"
   - `TicketAuditEventType Enum` → add "Category B — classification
     (Python Enum only)"
   - `IdentityAuditEventType Enum` → add "Category B — classification
     (Python Enum only)"
   - `SettingAuditEventType Enum` → add "Category B — classification
     (Python Enum only)"

**Verification criteria**:

- Each of the 7 enum sections now has a Category classification line
  matching the target pattern
- No enum values were changed, added, or removed
- Classifications are consistent with the Notes section (line 1549):
  Category A enums appear in the CHECK-constraint list; Category B enums
  do not

---

### Phase 5b: Extract inline enums into standalone sections

**Scope**: Extract 9 enums currently documented inline (no own heading)
into dedicated H4 sections with Category classification and value table.

**Actions**:

1. For each enum below, extract into a dedicated H4 section immediately
   after its owning table. Each extracted enum MUST include the Category
   classification line per the target pattern in Phase 5a.

   After extraction, update the column description in the owning table
   to use the brief reference pattern (matching existing conventions,
   e.g., `CVESource.status`): retain a bare value list + enum name as
   reference. Example: `"TicketStatus: New, Analysis, Analyzed,
   Resolved, Ignored, Duplicated"`. The extracted H4 section is the
   authoritative source for detailed value descriptions.

   Enums to extract:
   - `TicketStatus` (currently inline in Ticket table column `status`,
     line 1087: "New, Analysis, Analyzed, Resolved, Ignored, Duplicated")
     → extract to `#### TicketStatus Enum`. Category A — state-machine
     (VARCHAR + CHECK constraint `chk_ticket_status_valid`)
   - `CveState` (currently inline in CVE table column `cve_state`,
     line 436: "PUBLISHED or REJECTED") → extract to
     `#### CveState Enum`. Category A — state-machine (VARCHAR + CHECK
     constraint `chk_cve_cve_state_valid`)
   - `SubmissionRequestState` (currently inline text after
     SubmissionRequest table) → extract to
     `#### SubmissionRequestState Enum`. Category A — state-machine
     (VARCHAR + CHECK constraint
     `chk_submission_request_state_valid`)
   - `ReleaseRequestState` (currently inline text after ReleaseRequest
     table) → extract to `#### ReleaseRequestState Enum`. Category A —
     state-machine (VARCHAR + CHECK constraint
     `chk_release_request_state_valid`)
   - `BugownerType` (currently bold+table within PackageBugowner) →
     extract to `#### BugownerType Enum`. Category B — classification
     (Python Enum only)
   - `Role` enum values (currently within UserRole section) → extract to
     `#### Role Enum`. Category A — state-machine (VARCHAR + CHECK
     constraints: `chk_user_role_role_valid` on `user_role`,
     `chk_role_mapping_role_valid` on `role_mapping`)
   - `FetcherRunStatus` (currently inline in FetcherRun description) →
     extract to `#### FetcherRunStatus Enum`. Category A — state-machine
     (VARCHAR + CHECK constraint `chk_fetcher_run_status_valid`)
   - `FetcherRunTriggeredBy` (currently inline) → extract to
     `#### FetcherRunTriggeredBy Enum`. Category B — classification
     (Python Enum only)
   - `FetcherAuditEventType` (currently inline in FetcherAuditEvent) →
     extract to `#### FetcherAuditEventType Enum`. Category B —
     classification (Python Enum only)

2. Update the Notes section enum list to reflect any newly-extracted
   enums and verify the Category A/B classification is consistent

**Verification criteria**:

- Every enum value that existed before still exists in the file
- No enum values were changed, added, or removed
- The Notes section's enum classification list is updated to include
  newly-extracted enums
- Each new section follows the standardized pattern from Phase 5a

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
| CVESource `first_failed_at` detailed retry mechanism explanation | Line 458 (CVESource table, `first_failed_at` column Description) | `docs/features/platform/cve-source-failure-retry.md` | Reduce to column-level semantics (retaining write contract): "Timestamp when the current failure streak began. Set to now() on first transition to failure (when currently NULL). Preserved on subsequent failure writes. Cleared to NULL on success or missing writes. See `cve-service.md` (`record_source_status`) for write semantics and `cve-source-failure-retry.md` for the retry mechanism." Remove only the consumer-side explanation (evaluate_failed_cve_sources, 30-day window, stalled status) |
| CVESource "Derived predicate — stalled" block | Lines 464-470 (CVESource, after table) | `docs/features/platform/cve-source-failure-retry.md` | Reduce to formula + negative assertion: `**Derived predicate — "stalled"**: status = 'failure' AND first_failed_at < now() - 30 days. Not a stored column or ENUM value — a query-time predicate. See cve-source-failure-retry.md for consumers, threshold rationale, and operational guidance.` Remove consumer list (evaluate_failed_cve_sources, ?stalled filter) and operational guidance ("require operator investigation") — already in owning spec |

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
  equivalent or more detailed information (it does — lines 285-309)
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
| 5a | Low | Typo in Category classification line |
| 5b | Medium | Enum values accidentally changed during extraction |
| 6 | Medium | Information removed that doesn't exist in destination |
| 7 | Low | Broken anchor links |

Phase 3 is the highest-risk operation. Extra verification steps:

- Before: count total lines, count unique constraint blocks, count CHECK
  constraint blocks, count index blocks
- After: verify all counts match
- Perform a sorted-lines comparison to detect any accidental deletions
