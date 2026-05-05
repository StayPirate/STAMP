---
description: Interactive spec review and finding resolution workflow
---

Fully interactive command — no arguments accepted. Presents a recap of
all specs and their review status, then lets the user choose between
fixing open findings or running new reviews.

**Verbosity rule**: all data-gathering operations (scanning directories,
reading files, parsing findings) MUST be delegated to a Task agent
(subagent type `general` or `explore`). This keeps the main conversation
clean — the user sees only the formatted output and interactive prompts,
not the underlying tool calls. Never use Glob, Read, Grep, or Bash
directly in the main conversation for data-gathering steps; always
delegate to a subagent and use the returned data to present results.

**CRITICAL — gitignored files**: the files under `docs/drafts/` (including
`docs/drafts/review/`) are listed in `.gitignore` and will NOT appear in
Glob results or git-tracked file listings. Whenever a subagent needs to
discover or read files in `docs/drafts/`, it MUST use
`bash ls docs/drafts/review/` (or `bash ls docs/drafts/`) to list
existing files, then use the Read tool to read their content. Never rely
on Glob for files under `docs/drafts/`. This applies to ALL steps below
(data gathering, loading review files, loading context for reviews).

---

## Step 1: Gather data (silent)

Use the Task tool (subagent type `general`) to perform all of the
following in a single subagent session. Instruct the subagent to return
a structured result (JSON or clearly formatted text) containing:

1. **Spec list**: all `.md` filenames in `docs/features/` (without
   extension), sorted alphabetically
2. **Review status per spec**: for each spec, check if
   `docs/drafts/review/<name>.md` exists. If it does, parse it to
   extract:
   - Last reviewed date
   - Count of OPEN findings by severity (High, Medium, Low)
   - Count of RESOLVED findings
3. **Cross-spec patterns**: group OPEN findings across all specs by
   category and similar title/description keywords. If 2+ specs share
   findings with the same category AND overlapping keywords, flag them
   as a common pattern (include pattern description and affected specs)
4. **Specs with no review file**: mark as "Never reviewed"

The subagent should return all this data in a single message. Do NOT
present anything to the user until the subagent returns.

---

## Step 2: Display recap

Using the data from Step 1, present the recap table directly to the
user:

```
| Spec              | Last Review | High | Medium | Low | Total Open |
|-------------------|-------------|------|--------|-----|------------|
| tickets           | 2025-01-15  |    2 |      3 |   1 |          6 |
| package-tracking  | 2025-01-14  |    1 |      2 |   0 |          3 |
| rbac              | —           |    — |      — |   — |   (never)  |

Common patterns:
  - "Missing error path for concurrent updates" → tickets, package-tracking
  - "Unspecified pagination limits" → rbac, tickets
```

If no review files exist at all, show:

> No review files found yet. You can run reviews to generate findings.

---

## Step 3: Ask mode

Use the `question` tool to ask the user what they want to do. Options:

- **Fix findings** — description: "Resolve open findings one at a time"
  (only show this option if there are specs with OPEN findings)
- **Run reviews** — description: "Run reviewer agents on specs"

---

## Step 4a: Fix findings flow

### 4a.1. Ask which spec to fix

Use the `question` tool to present only the specs that have OPEN
findings. Each option label is the spec name; the description shows the
open finding counts (e.g., "2 High, 3 Medium, 1 Low — 6 open").

Sort by total open findings descending (most findings first).

### 4a.2. Load spec data (silent)

Use the Task tool (subagent type `general`) to:
- Read the chosen spec's review file (`docs/drafts/review/<name>.md`)
- Read the target spec (`docs/features/<name>.md`)
- Parse all OPEN findings with full details (ID, title, severity,
  category, description)
- Sort them by priority:
  1. Severity: High → Medium → Low
  2. Section order: Gap Analysis → Coherence → Design → Security

Return the sorted list of OPEN findings with all details.

### 4a.3. Fix loop (one finding at a time)

**Mode management**: the agent MUST remain in Plan mode during the
entire analysis and presentation phase (step 4a.3a). The user will
manually switch to Build mode when ready to apply the fix. After
implementing the fix and updating the review file (steps 4a.3c–4a.3e),
the agent MUST NOT present the next finding in the same message.
Instead, it asks the user if they want to continue (step 4a.3f) and
waits for their response. The user is responsible for switching back
to Plan mode (Tab key) before replying. The next finding is presented
only in the subsequent message, after the user has confirmed.

For the highest-priority OPEN finding:

#### 4a.3a. Present the finding with context (in Italian)

Present the finding to the user in Italian, including:

1. **Contesto del problema**: explain *what* the problem is, *why* it is
   a problem, and *what impact* it has on the spec or the system. Give
   the user enough context to understand the situation without having to
   re-read the entire spec. Reference the specific section(s) of the
   spec where the problem manifests.
2. **Soluzione proposta**: describe the proposed solution, list **all
   files** that will be modified and what changes in each. The fix is
   NOT limited to the target spec — it may touch any file in the
   repository (other specs in `docs/features/`, `docs/data-model.md`,
   `docs/api-spec.md`, `docs/architecture.md`, or any other relevant
   document).

Format:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Finding: <ID> — <Title> (<Severity>)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Contesto:
<Spiegazione in italiano di cosa è il problema, perché è un problema,
e quale impatto ha. Riferimenti alle sezioni specifiche della spec.>

Soluzione proposta:
<Descrizione in italiano della soluzione proposta.>

File coinvolti:
  - <file-path> — <cosa cambia>
  - <file-path> — <cosa cambia>
  - ...

Approvi questa soluzione? [sì / modificare / saltare]
```

#### 4a.3b. Wait for user decision

- **sì** (or approval): wait for the user to switch to Build mode
  before implementing the fix. Do NOT start editing files while still
  in Plan mode.
- **modificare**: ask what the user wants to change, adjust the
  proposal, and present it again (remain in Plan mode)
- **saltare**: skip this finding, move to the next one (remain in Plan
  mode)

#### 4a.3c. Implement the fix

CRITICAL: This step is MANDATORY. You MUST NOT proceed to updating the
review file (step 4a.3d) without first implementing the approved
solution in the actual spec/documentation files. Marking a finding as
RESOLVED without applying the fix is a bug. The implementation IS the
fix — the review file update is merely a record of it.

Edit all affected files to implement the approved solution. This may
include:
- Modifying the target spec (`docs/features/<name>.md`)
- Modifying other specs referenced by the finding
- Modifying cross-cutting documents (`docs/data-model.md`,
  `docs/api-spec.md`, `docs/architecture.md`, etc.)
- Any other documentation file relevant to the fix

After editing, verify that every file listed in "File coinvolti" from
the proposal has been modified. If a listed file was not changed, either
apply the missing change or explain to the user why it was not needed.

#### 4a.3d. Update the review file

PRECONDITION: Only mark a finding as RESOLVED after the fix has been
implemented (step 4a.3c). If for any reason the fix was NOT applied to
the spec/documentation files, the finding MUST remain OPEN. The
Resolution field must reference the actual changes made (file paths and
what was changed), not just state the intent.

Mark the finding as RESOLVED in the review file:

```markdown
### <ID> — <Title> (<Severity>)

**Category**: <category>
**Status**: RESOLVED
**Resolution**: <Short description of what was changed and where> (<YYYY-MM-DD>)

<Original detailed description of the finding>
```

#### 4a.3e. Update README index

Update `docs/drafts/review/README.md`:
- Recalculate the OPEN finding counts for this spec's row
- Update the Total row

#### 4a.3f. Ask to continue (do NOT present next finding yet)

After completing the fix, ask the user:

> "Finding risolto. Continuo con il prossimo? (ricorda di passare a Plan
> con Tab prima di rispondere)"

CRITICAL: Do NOT present the next finding in this same message. Stop
here and wait for the user's reply. The next finding (step 4a.3a) is
presented only in the following message, after the user confirms. This
gives the user the opportunity to switch back to Plan mode before the
next finding is shown.

If the user says yes, present the next highest-priority OPEN finding
from step 4a.3a. If no, show a brief summary of what was resolved in
this session.

---

## Step 4b: Run reviews flow

### 4b.1. Ask which spec to review

Use the `question` tool to present the available choices. Options in
this exact order:

1. **ALL** — description: "Run review on all specs sequentially"
2. Then each spec from `docs/features/` in alphabetical order, with
   description:
   - If previously reviewed: "Last review: <date> — <N> open findings"
   - If never reviewed: "Never reviewed"

Single selection only.

### 4b.2. Execute review

If the user selects **ALL**: process all specs in `docs/features/` in
alphabetical order, applying the review procedure (below) to each one.

If the user selects a specific spec: apply the review procedure to that
spec only.

### Review procedure (for each target spec)

#### Load context

Use the Task tool (subagent type `general`) to read:

1. The target spec: `docs/features/<name>.md`
2. All specs referenced by the target (look for links like
   `docs/features/other-spec.md` or mentions of "see <spec-name>")
3. Cross-cutting documents (always load):
   - `docs/data-model.md`
   - `docs/api-spec.md`
   - `docs/architecture.md`
4. Existing review file if present (`docs/drafts/review/<name>.md`) —
   to identify previously RESOLVED findings

The subagent should return the content of all loaded files.

#### Run reviewers in sequence

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

#### Write findings file

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
- For previously RESOLVED findings: if the resolution is still valid
  in the current spec, keep the finding with status RESOLVED and
  include the resolution text. If the spec has regressed, reopen it
  as OPEN
- Each finding MUST have enough detail for the user to understand and
  act on it without re-running the reviewer

#### Update README index

After writing the findings file, update `docs/drafts/review/README.md`:
- Update the row for this spec in the summary table with actual counts
- Update the "Last Review" column with today's date
- Recalculate the Total row

### 4b.3. Final report

After all selected specs are processed, output a summary to the user:
- How many specs were reviewed
- Total findings by severity
- Which specs have High-severity findings requiring attention
