---
description: Run systematic spec reviews and fix open findings interactively
---

Review the feature specification(s) specified in $ARGUMENTS using all 4
reviewer agents in sequence, or interactively fix open findings from
previous reviews.

## Arguments

- `<spec-name>` — review a single spec (e.g., `tickets`, `rbac`)
- `all` — review all specs in `docs/features/` sequentially
- `list` — interactive mode: list all specs and let the user choose which
  one to review
- `fix` — interactive mode: recap open findings across all specs, choose
  one to work on, and resolve findings one at a time

---

## Mode 1: Review (`<spec-name>` or `all`)

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

---

## Mode 2: List (`list`)

Interactive spec selection for review.

### 1. Enumerate available specs

List all `.md` files in `docs/features/` and extract spec names (filename
without the `.md` extension). Sort alphabetically.

### 2. Check existing review status

For each spec, check if `docs/drafts/review/<name>.md` exists. If it does,
parse it to extract:
- Last reviewed date
- Count of OPEN findings by severity (High/Medium/Low)

### 3. Present interactive choice

Use the `question` tool to present the user with the list of specs. Each
option label is the spec name; the description includes review status info:
- If previously reviewed: `Last review: <date> — <N> open findings (<H> High, <M> Medium, <L> Low)`
- If never reviewed: `Never reviewed`

Allow single selection only.

### 4. Run review on selected spec

Once the user selects a spec, proceed with Mode 1 (Review) using the
selected spec name as the target. Follow all steps in Mode 1 starting
from step 2.

---

## Mode 3: Fix (`fix`)

Interactive mode for resolving open findings from previous reviews.

### 1. Prerequisites

Scan `docs/drafts/review/` for `.md` files (excluding `README.md`).
If the directory does not exist or contains no review files, stop with:

> No review files found. Run `/review-spec <name>` or `/review-spec all`
> first to generate findings.

### 2. Parse all review files

For each review file:
- Parse all findings (ID, title, severity, category, status, description)
- Count OPEN findings by severity (High, Medium, Low)
- Collect finding titles and categories for cross-spec pattern detection

### 3. Cross-spec pattern detection

Group OPEN findings across all specs by category and similar
title/description keywords. If 2 or more specs share findings with the
same category AND overlapping keywords in their title or description,
flag them as a "common pattern". This helps the user decide which spec to
fix first (fixing a common pattern in one spec may inform the fix for
others).

### 4. Display recap table

Present the user with a summary table and any common patterns:

```
| Spec              | High | Medium | Low | Total Open |
|-------------------|------|--------|-----|------------|
| tickets           |    2 |      3 |   1 |          6 |
| package-tracking  |    1 |      2 |   0 |          3 |
| rbac              |    0 |      1 |   2 |          3 |

⚠ Common patterns detected:
  - "Missing error path for concurrent updates" affects: tickets, package-tracking
  - "Unspecified pagination limits" affects: rbac, tickets
```

If no specs have OPEN findings, inform the user:

> All findings are resolved. Nothing to fix.

### 5. Ask which spec to work on

Ask the user which spec they want to work on. Wait for their choice
before proceeding.

### 6. Fix loop (one finding at a time)

Load the chosen spec's review file and its target spec. Sort OPEN
findings by priority:
1. Severity: High → Medium → Low
2. Section order: Gap Analysis → Coherence → Design → Security

For the highest-priority OPEN finding:

#### 6a. Present the finding with context (in Italian)

Present the finding to the user in Italian, including:

1. **Contesto del problema**: explain *what* the problem is, *why* it is
   a problem, and *what impact* it has on the spec or the system. Give
   the user enough context to understand the situation without having to
   re-read the entire spec. Reference the specific section(s) of the spec
   where the problem manifests.
2. **Soluzione proposta**: describe the proposed solution, list **all
   files** that will be modified and what changes in each. The fix is NOT
   limited to the target spec — it may touch any file in the repository
   (other specs in `docs/features/`, `docs/data-model.md`,
   `docs/api-spec.md`, `docs/architecture.md`, or any other relevant
   document).

Format:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Finding: <ID> — <Title> (<Severity>)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📋 Contesto:
<Spiegazione in italiano di cosa è il problema, perché è un problema,
e quale impatto ha. Riferimenti alle sezioni specifiche della spec.>

💡 Soluzione proposta:
<Descrizione in italiano della soluzione proposta.>

File coinvolti:
  - <file-path> — <cosa cambia>
  - <file-path> — <cosa cambia>
  - ...

Approvi questa soluzione? [sì / modificare / saltare]
```

#### 6b. Wait for user decision

- **sì** (or approval): proceed to implement the fix
- **modificare**: ask what the user wants to change, adjust the proposal,
  and present it again
- **saltare**: skip this finding, move to the next one

#### 6c. Implement the fix

Edit all affected files to implement the approved solution. This may
include:
- Modifying the target spec (`docs/features/<name>.md`)
- Modifying other specs referenced by the finding
- Modifying cross-cutting documents (`docs/data-model.md`,
  `docs/api-spec.md`, `docs/architecture.md`, etc.)
- Any other documentation file relevant to the fix

#### 6d. Update the review file

Mark the finding as RESOLVED in the review file:

```markdown
### <ID> — <Title> (<Severity>)

**Category**: <category>
**Status**: RESOLVED
**Resolution**: <Short description of what was changed and where> (<YYYY-MM-DD>)

<Original detailed description of the finding>
```

#### 6e. Update README index

Update `docs/drafts/review/README.md`:
- Recalculate the OPEN finding counts for this spec's row
- Update the Total row

#### 6f. Continue or stop

Ask the user: "Continuo con il prossimo finding?" — if yes, repeat from
step 6a with the next highest-priority OPEN finding. If no, stop and
show a brief summary of what was resolved in this session.
