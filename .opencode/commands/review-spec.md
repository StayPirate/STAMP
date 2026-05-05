---
description: Run systematic spec reviews using all 4 reviewer agents
---

Review the feature specification(s) specified in $ARGUMENTS using all 4
reviewer agents in sequence, then write structured findings to
`docs/drafts/review/`.

## Arguments

- `<spec-name>` — review a single spec (e.g., `tickets`, `rbac`)
- `all` — review all specs in `docs/features/` sequentially

## Procedure

### 1. Determine target specs

- If `$ARGUMENTS` is `all`: list all `.md` files in `docs/features/` and
  process each one in alphabetical order
- Otherwise: the target is `docs/features/$ARGUMENTS.md`. Verify it exists;
  if not, report the error and stop

### 2. For each target spec

#### 2a. Load context

Read the following files:

1. The target spec: `docs/features/<name>.md`
2. All specs referenced by the target (look for links like
   `docs/features/other-spec.md` or mentions of "see <spec-name>")
3. Cross-cutting documents (always load these):
   - `docs/data-model.md`
   - `docs/api-spec.md`
   - `docs/architecture.md`

#### 2b. Load existing findings (if any)

If `docs/drafts/review/<name>.md` already exists, read it to identify
findings previously marked as RESOLVED. These will be preserved if the
resolution is still valid in the current spec.

#### 2c. Run reviewers in sequence

Execute each reviewer as a Task agent, one at a time, in this order:

1. **@spec-gap-analyzer** — identify uncovered functional cases: missing
   state transitions, unspecified error paths, boundary conditions, data
   lifecycle gaps, temporal/concurrency scenarios
2. **@spec-coherence-reviewer** — detect contradictions, conflicting
   business rules, and terminology inconsistencies with other specs
3. **@design-reviewer** — evaluate architectural decisions, complexity,
   edge cases, alternatives, long-term maintainability
4. **@security-reviewer** — find security vulnerabilities, insecure
   patterns, missing security controls in the spec's design

For each reviewer, pass the target spec content and all loaded context.
Collect all findings with: title, severity (High/Medium/Low), category,
and detailed description.

#### 2d. Write findings file

Write (or overwrite) `docs/drafts/review/<name>.md` with this structure:

```markdown
# Review: <spec-name>

**Spec**: `docs/features/<name>.md`
**Last reviewed**: <YYYY-MM-DD>
**Reviewers**: Gap Analysis, Coherence, Design, Security

---

## Gap Analysis

### <NAME>-GAP-01 — <Title> (High)

**Category**: <category>
**Status**: OPEN

<Detailed description of the finding>

### <NAME>-GAP-02 — <Title> (Medium)

...

---

## Coherence

### <NAME>-COH-01 — <Title> (Medium)

...

---

## Design

### <NAME>-DES-01 — <Title> (Medium)

...

---

## Security

### <NAME>-SEC-01 — <Title> (High)

...
```

Rules for writing the file:
- Use uppercase abbreviation of the spec name for finding IDs (e.g.,
  `TKT` for tickets, `PKG` for package-tracking, `RBAC` for rbac)
- Within each section, sort findings by severity: High first, then
  Medium, then Low
- For previously RESOLVED findings: if the resolution is still valid in
  the current spec, keep the finding with status RESOLVED and include the
  resolution text. If the spec has regressed, reopen it as OPEN
- Each finding MUST have enough detail for the user to understand and
  act on it without re-running the reviewer

#### 2e. Update README index

After writing the findings file, update `docs/drafts/review/README.md`:
- Update the row for this spec in the summary table with actual counts
- Update the "Last Review" column with today's date
- Recalculate the Total row

### 3. Final report

After all specs are processed, output a summary to the user:
- How many specs were reviewed
- Total findings by severity
- Which specs have High-severity findings requiring attention
