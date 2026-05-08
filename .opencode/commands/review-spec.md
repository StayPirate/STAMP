---
description: Spec review and finding resolution workflow (interactive or shortcut)
---

## Arguments (shortcut mode)

This command supports an optional shortcut syntax to skip the
interactive menus and jump directly into the fix flow:

```
/review-spec fix <target>
```

Where `<target>` is either a **spec name** or a **reviewer
abbreviation**.

### Target resolution

The command determines the target type in this order:

1. If `<target>` matches (case-insensitive) one of the known reviewer
   abbreviations → treated as a reviewer:

   | Abbreviation | Reviewer | Section |
   |--------------|----------|---------|
   | GAP | Gap Analysis | spec-gap-analyzer |
   | COH | Coherence | spec-coherence-reviewer |
   | DES | Design | design-reviewer |
   | SEC | Security | security-reviewer |
   | API | API Conventions | api-convention-reviewer |

2. Otherwise → treated as a spec name (matched against filenames in
   `docs/features/**/` without `.md` extension; filenames are unique
   across subdirectories)

### Shortcut flow

When invoked with `fix <target>`:

1. **Step 1 (data gathering)** runs normally — silently, via a Task
   subagent — to collect spec list and tracking state (from cache).
2. **Steps 2 and 3 are skipped entirely** (no recap table, no mode
   question).
3. **Step 4a.0 is skipped** (no patterns computation, no "By spec / By
   reviewer" question).
4. **Validation** is performed on the cached data (see below).
5. On success, the flow jumps directly to the fix loop:
   - **Spec target** → skip step 4a.1 (spec selection), proceed to step
     4a.2 (Load spec data) with the specified spec.
   - **Reviewer target** → skip step 4a-R.1 (reviewer selection),
     proceed to step 4a-R.2 (Load findings) with the specified reviewer.
6. Everything else (fix loop, presentation, mode management, fix
   implementation, review file update, README update) remains identical
   to the interactive flow.

### Validation errors

After Step 1 data gathering completes, validate the target using the
cached data in `.tracking.json`. If validation fails, output the error
message and stop — do NOT fall back to the interactive flow.

**If target is a spec name:**

- Spec does not exist in `docs/features/**/`:
  > Errore: la spec `<name>` non esiste in `docs/features/`.

- Spec exists but is disabled in `.tracking.json`:
  > Errore: la spec `<name>` è disabilitata. Abilitala prima con
  > `/review-spec` → Toggle spec tracking.

- Spec is enabled but has zero OPEN findings (cache is null or all
  open counts are 0):
  > Nessun finding OPEN per la spec `<name>`.

**If target is a reviewer abbreviation:**

- No OPEN findings for that reviewer across any enabled spec (sum the
  reviewer's H+M+L across all enabled specs' caches):
  > Nessun finding OPEN per `<Reviewer Name>` su spec abilitate.

### No-argument mode (interactive)

When invoked without arguments (`/review-spec` with no arguments), the
command operates in fully interactive mode as described below. Presents
a recap of all specs and their review status, then lets the user choose
between fixing open findings or running new reviews.

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

Use the Task tool (subagent type `explore`) to perform all of the
following in a single subagent session. Instruct the subagent to return
the final `.tracking.json` content (or a structured summary if no
changes were needed).

1. **Spec list**: all `.md` filenames in `docs/features/` subdirectories
   (use Glob `docs/features/**/*.md` to find all specs recursively;
   exclude files matching `docs/features/**/pages/*.md` — these are
   sub-pages of a parent spec, not independent specs). Use the filename
   without extension as the spec name (filenames are unique across
   subdirectories). Sort alphabetically.
2. **Tracking state**: read `docs/drafts/review/.tracking.json`. Handle
   these cases:
   - **File does not exist** (first run): create it with ALL specs
     currently in `docs/features/**/` set to `"enabled": true`, with
     auto-generated `abbr` and `"cache": null`. Write the file to disk
     immediately.
   - **File exists**: load it. For any spec in `docs/features/**/` that
     is NOT present in the JSON, add it as `"enabled": false` with
     auto-generated `abbr` and `"cache": null` (new spec discovered —
     disabled by default). Write the updated file back only if changed.
   - For any spec listed in the JSON that no longer exists in
     `docs/features/**/`, remove it from the JSON (stale entry cleanup).
   - Return the full `.tracking.json` content.
3. **No review file parsing at startup**: the `cache` field in
   `.tracking.json` is always trusted. It is updated by the command
   itself whenever a review file is written or modified (Steps 4a.3c,
   4b, 4c.4, 4d.2). The subagent MUST NOT read or parse review files
   during this step.

The subagent should return the `.tracking.json` content in a single
message. Do NOT present anything to the user until the subagent returns.

### `.tracking.json` format

```json
{
  "specs": {
    "tickets": {
      "enabled": true,
      "abbr": "TKT",
      "cache": {
        "last_review": "2026-05-06",
        "open": {
          "GAP": { "H": 1, "M": 2, "L": 0 },
          "COH": { "H": 0, "M": 1, "L": 0 },
          "DES": { "H": 0, "M": 0, "L": 1 },
          "SEC": { "H": 1, "M": 0, "L": 0 },
          "API": { "H": 0, "M": 0, "L": 0 }
        },
        "resolved": 8,
        "not_reviewed": []
      }
    },
    "rbac": {
      "enabled": true,
      "abbr": "RBAC",
      "cache": null
    },
    "pages": {
      "enabled": false,
      "abbr": "PAG",
      "cache": null
    }
  }
}
```

Field definitions:
- `enabled`: whether the spec is tracked for reviews
- `abbr`: uppercase abbreviation used in finding IDs (e.g., `TKT-GAP-01`)
- `cache`: review status summary, or `null` if never reviewed
  - `last_review`: date of last review (YYYY-MM-DD)
  - `open`: OPEN finding counts per section, per severity (H/M/L)
  - `resolved`: total count of RESOLVED findings
  - `not_reviewed`: array of section abbreviations still showing
    `_Not yet reviewed._` (e.g., `["SEC", "API"]`)

### Abbreviation derivation rules

The `abbr` field is generated automatically when a spec is first added
to `.tracking.json` and MUST NEVER be modified afterward (finding IDs
depend on it). Derivation rules:

1. Single-word spec, ≤4 letters: full name uppercased (`rbac` → `RBAC`)
2. Single-word spec, >4 letters: first 3 letters uppercased
   (`tickets` → `TKT`, `admin` → `ADM`)
3. Hyphenated spec, 4+ words: first letter of each word uppercased,
   max 4 chars (`ibs-codestream-release-detection` → `ICRD`)
4. Hyphenated spec, 2-3 words: take letters from each word to reach
   3-4 chars, prioritizing recognizability
   (`package-tracking` → `PKT`, `user-service` → `USVC`,
   `sso-authentication` → `SSOA`)
5. If collision with an existing `abbr`: append successive letters from
   the last word until unique (e.g., `ICRD` collides → `ICRE`)

---

## Step 2: Display recap

Using the cached data from `.tracking.json` (loaded in Step 1), present
the recap table directly to the user. Show only **enabled specs** —
disabled specs are not displayed in the recap (they remain accessible
via "Toggle spec tracking"). No file I/O is needed for this step.

### Enabled specs (main table)

Show only enabled specs, sorted alphabetically. Use the standard
two-row-per-spec format with emoji severity indicators:

```
| Spec             | GAP | COH | DES | SEC | API | Total | Last Review |
|------------------|-----|-----|-----|-----|-----|-------|-------------|
| tickets          |   3 |   1 |   1 |   2 | 🟢  |     7 | 2025-01-15  |
|                  | 1:🔴 2:🟠 | 1:🟡 | 1:🟠 | 1:🔴 1:🟠 |  | 2:🔴 4:🟠 1:🟡 |  |
| package-tracking |   2 | 🟢  |   1 |  —  |  —  |     3 | 2025-01-14  |
|                  | 1:🟠 1:🟡 |  | 1:🔴 |  |  | 1:🔴 1:🟠 1:🟡 |  |
| rbac             |  —  |  —  |  —  |  —  |  —  | (never) | —         |
| **Total**        |   5 |   1 |   2 |   2 | 🟢  |    10 | —           |
|                  | ... |     |     |     |     |       |             |
```

The **Total** row sums only enabled specs.

Rendering rules (derived from `cache`):
- `cache: null` → all cells are `—`, Last Review is `—`, Total shows
  `(never)`
- Section in `not_reviewed` array → cell is `—`
- Section has all H/M/L = 0 and is NOT in `not_reviewed` → cell is `🟢`
- Section has H+M+L > 0 → cell shows the total count; sub-row shows
  severity breakdown

### Edge cases

If no enabled specs have a `cache` (all are `null`), show:

> No review data found for enabled specs. You can run reviews to
> generate findings.

If all specs are disabled, show:

> All specs are currently disabled. Use "Toggle spec tracking" to enable
> specs before running reviews.

---

## Step 3: Ask mode

Use the `question` tool to ask the user what they want to do. Determine
which options to show based on the cached data in `.tracking.json`.
Options:

- **Fix findings** — description: "Resolve open findings one at a time"
  (only show if there are enabled specs with OPEN findings in cache)
- **Run reviews** — description: "Run all reviewer agents on specs"
  (only show if there is at least one enabled spec)
- **Run single reviewer** — description: "Run one specific reviewer on
  one or all specs"
  (only show if there is at least one enabled spec)
- **Toggle spec tracking** — description: "Enable or disable spec
  tracking"

---

## Step 4a: Fix findings flow

### 4a.0. Compute cross-spec patterns and ask grouping mode

Use the Task tool (subagent type `general`) to compute cross-spec
patterns across **enabled specs only**:
- Read all review files in `docs/drafts/review/` (use
  `bash ls docs/drafts/review/` to discover them)
- For each enabled spec that has a review file, extract all OPEN
  findings (title, category, description keywords)
- Group findings by category and similar title/description keywords.
  If 2+ specs share findings with the same category AND overlapping
  keywords, flag them as a common pattern
- Return: list of patterns (pattern description + affected spec names)

If patterns are found, present them to the user:

```
Pattern comuni tra spec:
  - "Missing error path for concurrent updates" → tickets, package-tracking
  - "Unspecified pagination limits" → rbac, tickets
```

If no patterns are found, skip this section (show nothing).

Then use the `question` tool to ask how the user wants to work on
findings. Options:

- **By spec** — description: "Work on all findings of a specific spec"
- **By reviewer** — description: "Work on all findings of a specific
  reviewer, across all specs"

Single selection only.

If the user selects **By spec**, proceed to step 4a.1 (below).
If the user selects **By reviewer**, proceed to step 4a-R (below).

---

### By-spec flow

### 4a.1. Ask which spec to fix

Use the `question` tool to present only the **enabled** specs that have
OPEN findings (derive from cache in `.tracking.json`). Disabled specs
are never shown here. Each option label is the spec name; the
description shows the open finding counts (e.g.,
"2 High, 3 Medium, 1 Low — 6 open").

Sort by total open findings descending (most findings first).

### 4a.2. Load spec data (silent)

Use the Task tool (subagent type `general`) to:
- Read the chosen spec's review file (`docs/drafts/review/<name>.md`)
- Read the target spec (`docs/features/**/<name>.md`)
- Parse all OPEN findings with full details (ID, title, severity,
  category, description)
- Sort them by priority:
  1. Severity: High → Medium → Low
  2. Section order: Gap Analysis → Coherence → Design → Security → API Conventions

The subagent returns ONLY the parsed findings as structured data (ID,
title, severity, category, description text). It MUST NOT return raw
file content of the spec or review file — those remain in the
subagent's context only.

Return the sorted list of OPEN findings with all details.

### 4a.3. Fix loop (one finding at a time)

**Mode management**: the agent MUST remain in Plan mode during the
entire analysis and presentation phase (step 4a.3a). The user will
manually switch to Build mode when ready to apply the fix. After the
fix is implemented by the subagent (step 4a.3c), the agent MUST NOT
present the next finding in the same message. Instead, it asks the user
if they want to continue (step 4a.3d) and waits for their response.
The user is responsible for switching back to Plan mode (Tab key)
before replying. The next finding is presented only in the subsequent
message, after the user has confirmed.

For the highest-priority OPEN finding:

#### 4a.3a. Present the finding with context (in Italian)

Before formulating the proposed solution, apply the placement self-check
from Guardrail 21 (tests A–D). If the fix involves adding new rules or
patterns to a spec, verify that the proposed destination is the most
appropriate location. If placement is ambiguous, include the options in
the proposal for the user to decide.

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

Approvi questa soluzione? [sì / modificare / saltare / basta]
```

#### 4a.3b. Wait for user decision

- **sì** (or approval): wait for the user to switch to Build mode
  before implementing the fix. Do NOT start editing files while still
  in Plan mode.
- **modificare**: ask what the user wants to change, adjust the
  proposal, and present it again (remain in Plan mode)
- **saltare**: skip this finding, move to the next one (remain in Plan
  mode). The finding remains OPEN with no changes.
- **basta**: exit the fix loop. Show a brief summary of the session:
  how many findings were resolved, how many skipped, how many remain
  OPEN for this spec.

#### 4a.3c. Implement the fix, update review file, update README

CRITICAL: This step is MANDATORY. You MUST NOT mark a finding as
RESOLVED without implementing the approved solution in the actual
spec/documentation files. Marking a finding as RESOLVED without
applying the fix is a bug. The implementation IS the fix — the review
file update is merely a record of it.

Use the Task tool (subagent type `general`) to perform ALL of the
following in a single subagent session:

1. **Implement the approved solution**: read and edit all files listed
   in "File coinvolti". Verify every listed file is modified. If a
   listed file turns out not to need changes, skip it but include the
   reason in the confirmation.
2. **Update the review file**: mark the finding as RESOLVED in
   `docs/drafts/review/<name>.md` with:
   ```markdown
   ### <ID> — <Title> (<Severity>)

   **Category**: <category>
   **Status**: RESOLVED
   **Resolution**: <Short description of what was changed and where> (<YYYY-MM-DD>)

   <Original detailed description of the finding>
   ```
3. **Update README index**: update `docs/drafts/review/README.md`
   following the README Index Layout rules (end of this document).
4. **Update cache in `.tracking.json`**: recalculate the `cache` field
   for this spec (decrement the OPEN count for the resolved finding's
   section and severity, increment `resolved` count). Write the
   updated `.tracking.json` to disk.

Pass to the subagent:
- The finding ID, title, severity, category, full description
- The approved solution (from the presentation in 4a.3a)
- The list of files to modify and what to change in each
- The spec name, abbreviation, and review file path

The subagent returns a brief confirmation:
- List of files modified (path + one-line summary of change)
- New open/resolved counts for the review file
- Any file that was NOT modified and why

CRITICAL: the subagent MUST implement the fix FIRST, then update the
review file and cache. If the fix fails for any reason, the finding
MUST remain OPEN and the cache MUST NOT be updated.

Present the subagent's confirmation to the user.

#### 4a.3d. Ask to continue (do NOT present next finding yet)

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

### By-reviewer flow

### 4a-R.1. Ask which reviewer

Use the `question` tool to present only the reviewers that have at least
one OPEN finding across **enabled** specs (derive from cache in
`.tracking.json`). Disabled specs' findings are excluded from this
count. Each option label is the reviewer name; the description shows
the total open finding count and number of affected specs (e.g.,
"9 open findings across 3 specs").

Options (in this order, skipping those with zero OPEN findings):

1. **Gap Analysis**
2. **Coherence**
3. **Design**
4. **Security**
5. **API Conventions**

Single selection only.

### 4a-R.2. Load findings (silent)

Use the Task tool (subagent type `general`) to:
- Read all review files in `docs/drafts/review/` (use
  `bash ls docs/drafts/review/` to discover them)
- Extract all OPEN findings from the section corresponding to the chosen
  reviewer, across **enabled specs only** (skip disabled specs)
- For each finding: include the spec name, finding ID, title, severity,
  category, and full description
- Sort by:
  1. Severity: High → Medium → Low
  2. Spec name alphabetical

The subagent returns ONLY the parsed findings as structured data. It
MUST NOT return raw file content — those remain in the subagent's
context only.

Return the sorted list grouped by spec, with all details.

### 4a-R.3. Fix loop (one finding at a time)

**Mode management**: same rules as step 4a.3 — remain in Plan mode
during analysis/presentation, user switches to Build mode for fixes.

**Context management**: for each finding, the agent needs the context
of the spec it belongs to. Context is loaded via a Task agent that
also formulates the proposal. A separate fresh Task agent handles fix
implementation. This keeps spec content out of the main conversation.

#### 4a-R.3a. Present the finding with context (in Italian)

Use the Task tool (subagent type `general`, **new session** when the
spec changes) to silently:
- Load the target spec (`docs/features/**/<name>.md`)
- Load all specs referenced by the target
- Load cross-cutting documents (`docs/data-model.md`, `docs/api-spec.md`,
  `docs/architecture.md`)
- Apply the placement self-check from Guardrail 21 (tests A–D)
- Analyze the finding in context and formulate:
  1. The "Contesto del problema" explanation (in Italian)
  2. The "Soluzione proposta" with all files involved (in Italian)

The subagent returns ONLY the formatted presentation text (context +
proposal + file list). It does NOT return raw file contents.

Present the subagent's output to the user using the standard format
with the added **Spec** line:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Finding: <ID> — <Title> (<Severity>)
Spec: <spec-name>
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

Approvi questa soluzione? [sì / modificare / saltare / basta]
```

For subsequent findings of the **same** spec, reuse the same analysis
Task agent session (via `task_id`) to avoid reloading files. When the
spec changes, start a **fresh** Task session.

#### 4a-R.3b. Wait for user decision

Same options as step 4a.3b (sì / modificare / saltare / basta).

#### 4a-R.3c. Implement the fix, update review file, update README

Use a **FRESH** Task subagent (type `general`, new session) to
implement the approved solution, update the review file, update README,
and update cache — same as step 4a.3c. Do NOT reuse the analysis
subagent's session for implementation — this keeps the analysis
session's context clean for presenting subsequent findings of the same
spec.

Pass to the subagent the same data as step 4a.3c.

#### 4a-R.3d. Ask to continue / spec transition

After completing a fix, check whether the next finding belongs to the
same spec or a different one.

**Same spec**: ask as usual:

> "Finding risolto. Continuo con il prossimo? (ricorda di passare a Plan
> con Tab prima di rispondere)"

**Different spec**: inform the user and ask for confirmation:

> "Finding risolto. Il prossimo finding è in `<next-spec-name>`. Carico
> il contesto della nuova spec (sessione fresca). Continuo? (ricorda di
> passare a Plan con Tab prima di rispondere)"

If the user says yes and the spec is changing, start a **fresh** Task
session for the new spec (do NOT reuse the previous `task_id`). This
ensures clean context without accumulation from the previous spec.

If the user says no, show a brief summary of what was resolved in this
session (findings resolved, grouped by spec).

CRITICAL: Do NOT present the next finding in this same message. Stop
and wait for the user's reply. The next finding is presented only in
the following message.

---

## Step 4b: Run reviews flow

### 4b.1. Ask which spec to review

Use the `question` tool to present the available choices. Only
**enabled** specs are shown — disabled specs are excluded. Options in
this exact order:

1. **ALL** — description: "Run review on all enabled specs sequentially"
2. Then each **enabled** spec from `docs/features/**/` in alphabetical
   order, with description:
   - If previously reviewed: "Last review: <date> — <N> open findings"
   - If never reviewed: "Never reviewed"

Single selection only.

### 4b.2. Execute review

If the user selects **ALL**: process all **enabled** specs in
`docs/features/**/` in alphabetical order, applying the review procedure
(below) to each one.

If the user selects a specific spec: apply the review procedure to that
spec only.

### Review procedure (for each target spec)

#### Run all reviewers in parallel

Launch **5 Task agents in parallel** (one per reviewer, all in a single
message with multiple Task tool calls). Each agent independently:

1. Reads the target spec: `docs/features/**/<name>.md`
2. Reads all specs referenced by the target (look for links like
   `docs/features/<domain>/<spec>.md` or mentions of "see <spec-name>")
3. Reads cross-cutting documents (always):
   - `docs/data-model.md`
   - `docs/api-spec.md`
   - `docs/architecture.md`
4. Reads the existing review file if present
   (`docs/drafts/review/<name>.md`) — to preserve RESOLVED findings
   in its own section
5. Executes its review
6. Returns structured findings: list of objects with fields:
   `id_number` (sequential within section), `title`, `severity`,
   `category`, `description`, `status` (OPEN or RESOLVED with
   resolution text)

Agent mapping:

| Reviewer | Subagent type |
|----------|---------------|
| Gap Analysis | `spec-gap-analyzer` |
| Coherence | `spec-coherence-reviewer` |
| Design | `design-reviewer` |
| Security | `security-reviewer` |
| API Conventions | `api-convention-reviewer` |

Each reviewer MUST also check previously RESOLVED findings in its
section: if the resolution is still valid in the current spec, keep
RESOLVED; if the spec has regressed (the section modified by the
resolution was reverted or the content reintroduces the original
problem), reopen as OPEN. Cosmetic rewording or structural
reorganization of a correct fix does NOT constitute regression.

#### Write findings file, update README, update cache

After all 5 reviewers return, use the Task tool (subagent type
`general`) to:

1. Assemble and write (or overwrite) `docs/drafts/review/<name>.md`
   using the findings from all 5 reviewers
2. Use the `abbr` field from `.tracking.json` for finding IDs
3. Update `docs/drafts/review/README.md` following the README Index
   Layout rules (end of this document)
4. Recalculate and update the `cache` field for this spec in
   `.tracking.json`
5. Return: summary (findings per section, per severity, total open,
   total resolved)

Pass to the subagent: the 5 sets of structured findings + spec name +
abbreviation + today's date + path to the spec file (for the header).
Do NOT pass raw spec content.

The review file structure:

```markdown
# Review: <spec-name>

**Spec**: `docs/features/**/<name>.md`
**Last reviewed**: <YYYY-MM-DD>
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

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

---

## API Conventions

### <NAME>-API-01 — <Title> (High)

...
```

Rules for writing the file:
- Use the `abbr` field from `.tracking.json` for finding IDs (e.g.,
  `TKT-GAP-01`)
- Within each section, sort findings by severity: High first, then
  Medium, then Low
- Each finding MUST have enough detail for the user to understand and
  act on it without re-running the reviewer

### 4b.3. Final report

After all selected specs are processed, output a summary to the user:
- How many specs were reviewed
- Total findings by severity
- Which specs have High-severity findings requiring attention

---

## Step 4c: Run single reviewer flow

### 4c.1. Ask which reviewer

Use the `question` tool to present the available reviewers. Options in
this exact order:

1. **Gap Analysis** — description: "Uncovered functional cases, missing
   state transitions, error paths, boundary conditions"
2. **Coherence** — description: "Contradictions and terminology
   inconsistencies with other specs"
3. **Design** — description: "Architectural decisions, complexity, edge
   cases, alternatives, maintainability"
4. **Security** — description: "Security vulnerabilities, insecure
   patterns, missing controls"
5. **API Conventions** — description: "API endpoint definitions
   conformity (error codes, naming, pagination, envelope)"

Single selection only.

### 4c.2. Ask which spec

Use the `question` tool to present the available choices. Only
**enabled** specs are shown — disabled specs are excluded. Options in
this exact order:

1. **ALL** — description: "Run reviewer on all enabled specs in parallel"
2. Then each **enabled** spec from `docs/features/**/` in alphabetical
   order, with description:
   - If previously reviewed: "Last review: <date> — <N> open findings"
   - If never reviewed: "Never reviewed"

Single selection only.

### 4c.3. Execute review

#### Load context and run reviewer

For each target spec, use the Task tool (subagent type matching the
chosen reviewer) to:

1. Read the target spec: `docs/features/**/<name>.md`
2. Read all specs referenced by the target (look for links like
   `docs/features/<domain>/<spec>.md` or mentions of "see <spec-name>")
3. Read cross-cutting documents (always):
   - `docs/data-model.md`
   - `docs/api-spec.md`
   - `docs/architecture.md`
4. Read existing review file if present (`docs/drafts/review/<name>.md`)
   — to identify previously RESOLVED findings in the reviewer's section
5. Execute the review and return structured findings (id_number, title,
   severity, category, description, status)

Each reviewer MUST also check previously RESOLVED findings in its
section: if the resolution is still valid, keep RESOLVED; if the spec
has regressed (the section modified by the resolution was reverted or
the content reintroduces the original problem), reopen as OPEN.
Cosmetic rewording or structural reorganization of a correct fix does
NOT constitute regression.

**Parallelism**: if the user selected **ALL**, launch one Task agent
**per spec in parallel** (multiple Task tool calls in a single message).
Each agent independently loads its own spec + references + cross-cutting
docs and runs the chosen reviewer. If the user selected a single spec,
launch one Task agent.

Reviewer-to-agent mapping:

| Selection | Subagent type |
|-----------|---------------|
| Gap Analysis | `spec-gap-analyzer` |
| Coherence | `spec-coherence-reviewer` |
| Design | `design-reviewer` |
| Security | `security-reviewer` |
| API Conventions | `api-convention-reviewer` |

### 4c.4. Write/update findings files, update README, update cache

After all reviewer Task agents return their findings, use the Task
tool (subagent type `general`) to process ALL reviewed specs in a
single session:

1. For each reviewed spec, read the existing review file (if any)
2. Replace **only** the section (`## <Section Name>`) corresponding to
   the executed reviewer with the new findings; preserve all other
   sections untouched
3. Update file headers:
   - Set `**Last reviewed**` to today's date
   - Ensure the `**Reviewers**` line lists all sections that have been
     populated (not placeholders)
4. If no review file exists, create it with the standard skeleton (all
   sections as `_Not yet reviewed._`) and populate the reviewed section
5. Use the `abbr` field from `.tracking.json` for finding IDs
6. After processing all specs, update `docs/drafts/review/README.md`
   following the README Index Layout rules
7. Recalculate and update the `cache` field for each reviewed spec in
   `.tracking.json`
8. Return: per-spec summary (findings count, severities)

Pass to the subagent: all findings grouped by spec + spec names +
abbreviations + reviewer name + today's date. Do NOT pass raw spec
content.

#### New file skeleton (when review file does not exist)

```markdown
# Review: <spec-name>

**Spec**: `docs/features/**/<name>.md`
**Last reviewed**: <YYYY-MM-DD>
**Reviewers**: <Name of the executed reviewer>

---

## Gap Analysis

_Not yet reviewed._

---

## Coherence

_Not yet reviewed._

---

## Design

_Not yet reviewed._

---

## Security

_Not yet reviewed._

---

## API Conventions

_Not yet reviewed._
```

Then replace the placeholder (`_Not yet reviewed._`) in the section
corresponding to the executed reviewer with the actual findings.

#### Finding format

Findings within the populated section follow the same format as Step 4b:

```markdown
### <NAME>-<PREFIX>-01 — <Title> (Severity)

**Category**: <category>
**Status**: OPEN

<Detailed description of the finding>
```

Section-to-prefix mapping:

| Section | Prefix |
|---------|--------|
| Gap Analysis | GAP |
| Coherence | COH |
| Design | DES |
| Security | SEC |
| API Conventions | API |

Rules:
- Use the `abbr` field from `.tracking.json` for finding IDs
- Within the section, sort findings by severity: High first, then
  Medium, then Low
- Each finding MUST have enough detail for the user to understand and
  act on it without re-running the reviewer

### 4c.5. Final report

Output a summary to the user:
- Which reviewer was executed
- How many specs were processed
- Total findings by severity
- Which specs have High-severity findings requiring attention

---

## Step 4d: Toggle spec tracking flow

### 4d.1. Present current state and ask for toggle

Use the `question` tool with `multiple: true` to allow the user to
select one or more specs to toggle. Present ALL specs (both enabled and
disabled), sorted alphabetically. Derive descriptions from the cached
data in `.tracking.json`. Each option:

- **Label**: the spec name
- **Description**: current state and context, e.g.:
  - `"Currently: ENABLED — 5 open findings"`
  - `"Currently: ENABLED — never reviewed"`
  - `"Currently: DISABLED — 3 findings frozen (last review: 2026-05-06)"`
  - `"Currently: DISABLED — never reviewed"`

The user selects the specs they want to **flip** (enabled → disabled, or
disabled → enabled).

### 4d.2. Apply toggles

Use the Task tool (subagent type `general`) to perform all toggles in
a single session. For each selected spec, flip its state:

#### Enabling a spec (disabled → enabled)

If the spec has an existing review file with OPEN findings (check via
cache — if cache is non-null and has open counts > 0):
1. Read the spec (`docs/features/**/<name>.md`)
2. Read the review file (`docs/drafts/review/<name>.md`)
3. For each OPEN finding, check if it is still applicable to the
   current version of the spec
4. Findings that are no longer applicable (the spec has been changed to
   address the issue, or the relevant section no longer exists) are
   marked as RESOLVED with:
   ```
   **Status**: RESOLVED
   **Resolution**: Auto-resolved: finding no longer applicable after spec changes (<YYYY-MM-DD>)
   ```
5. Findings that are still applicable remain OPEN
6. Update the review file on disk with the new statuses
7. Recalculate and update the `cache` field in `.tracking.json`

If the spec has no review file or no OPEN findings (cache is null or
all open counts are 0): simply flip the `enabled` field. No validation
needed.

#### Disabling a spec (enabled → disabled)

1. Flip `enabled` to `false` in `.tracking.json`
2. The review file (if it exists) is left untouched on disk — findings
   are preserved ("frozen")
3. The `cache` field is left as-is (frozen state)

#### Final operations (always)

After processing all toggles:
1. Write the updated `.tracking.json` to disk
2. Update `docs/drafts/review/README.md` following the README Index
   Layout rules (recalculate Total row based on new enabled set)
3. Return: per-spec summary (what was flipped, validation results)

### 4d.3. Confirm

Present the subagent's results to the user:

```
Tracking aggiornato:
  ✓ pages: DISABLED → ENABLED (3 findings validated, 1 auto-resolved)
  ✓ references: ENABLED → DISABLED (2 findings frozen)
  ✓ new-feature: DISABLED → ENABLED (never reviewed)
```

---

## README Index Layout

This section defines the canonical layout rules for
`docs/drafts/review/README.md`. All steps that update the README index
MUST follow these rules.

### Structure

The README has two sections:
1. **Main table** — enabled specs only
2. **Disabled section** — disabled specs (shown below the main table)

### Main table (enabled specs)

Two-row-per-spec format:

- **Main row**: spec name (as a link to the review file), OPEN finding
  count per reviewer section (GAP/COH/DES/SEC/API), total open, last
  review date. Use `—` for sections that have never been reviewed
  (`_Not yet reviewed._` in the review file). Use `🟢` for sections
  that have been reviewed but have zero OPEN findings (either no
  findings were raised, or all findings are RESOLVED).
- **Sub-row**: severity breakdown per section using colored circles:
  `🔴` = High, `🟠` = Medium, `🟡` = Low. Format: `N:🔴 N:🟠 N:🟡`,
  separated by spaces, omitting severities with zero count. Leave cell
  empty if the main row is `—` or `🟢`.
- **Total row** at the bottom: sum of OPEN findings across **enabled
  specs only**, with severity sub-row.

### Disabled section

Shown below the main table, preceded by a `### Disabled specs` heading.
Uses the same table structure but with **text-only severity format**
(no emoji):

- **Main row**: same columns, same rules for `—` and counts. Use `-`
  (text dash) instead of `🟢` for reviewed-with-zero-findings.
- **Sub-row**: severity format is `H:N M:N L:N` (space-separated,
  omit severities with zero count). Leave cell empty if main row is
  `—` or `-`.
- **No Total row** for disabled specs.
- If a spec has never been reviewed: all cells are `—`.

### When to update

The README index is updated:
- After writing/modifying any findings file (steps 4a.3c, 4b review
  procedure, 4c.4)
- After toggling spec tracking (step 4d)
- Always recalculate the Total row based on current enabled specs
