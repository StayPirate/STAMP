---
description: Spec review and finding resolution workflow (interactive or shortcut)
---

Execute this spec review workflow NOW. Do not summarize, discuss, or ask
clarifying questions about these instructions — follow them step by step.

CRITICAL EXECUTION RULES (must obey BEFORE reading the rest):
1. ALL data-gathering work (scanning directories, reading files, writing
   .tracking.json) MUST be delegated to a Task agent (subagent). NEVER
   do file I/O directly in the main conversation. Task agents CAN write
   files even when the main conversation is in Plan mode — delegation is
   the mechanism for writes.
2. Files under docs/drafts/ are GITIGNORED. Glob will NOT find them.
   Task agents MUST use `bash ls docs/drafts/review/` to discover files
   there, then Read to read them. NEVER use Glob for docs/drafts/.
3. The user sees ONLY formatted output and interactive prompts from you.
   All tool calls for data gathering happen inside Task agents invisibly.
4. Reference files for subagent procedures are in
   `.opencode/commands/review-spec/`. Subagents MUST Read the specific
   files they need (listed in each step below).

The user's arguments are (between the triple backticks):
```
$ARGUMENTS
```

DECISION LOGIC — read carefully:
- If the arguments above are EMPTY or BLANK: run the INTERACTIVE flow
  (Steps 1 → 2 → 3 → chosen step 4).
- If the arguments start with `fix` (e.g., "fix tickets" or
  "fix GAP"): run the **Fix shortcut** flow below.
- If the arguments start with `refresh` (e.g., "refresh user-management"
  or "refresh all"): run the **Refresh shortcut** flow below.
- If the arguments start with `compact` (e.g., "compact all" or
  "compact user-management"): run the **Compact shortcut** flow below.

IMPORTANT: The examples and syntax descriptions below (like
"/review-spec fix <target>") are DOCUMENTATION for how shortcuts work.
They are NOT actual arguments. Only the text between the triple
backticks above represents what the user actually typed.

---

## Shortcut mode

### Fix shortcut

Syntax: `/review-spec fix <target>` where `<target>` is a **spec name**
or a **reviewer abbreviation**.

#### Target resolution

1. If `<target>` matches (case-insensitive) a reviewer abbreviation →
   treated as a reviewer:

   | Abbreviation | Reviewer | Section |
   |--------------|----------|---------|
   | GAP | Gap Analysis | spec-gap-analyzer |
   | COH | Coherence | spec-coherence-reviewer |
   | DES | Design | design-reviewer |
   | SEC | Security | security-reviewer |
   | API | API Conventions | api-convention-reviewer |

2. Otherwise → treated as a spec name (matched against filenames in
   `docs/features/**/` without `.md` extension)

#### Shortcut flow

1. Step 1 runs normally (data gathering via subagent).
2. Steps 2-3 are skipped (no recap table, no mode question).
3. Step 4a.0 is skipped (no patterns, no grouping question).
4. Validate the target against cached data in `.tracking.json`:
   - **Spec not found**: `Errore: la spec '<name>' non esiste in 'docs/features/'.`
   - **Spec disabled**: `Errore: la spec '<name>' è disabilitata. Abilitala prima con '/review-spec' → Toggle spec tracking.`
   - **Spec has zero OPEN findings**: `Nessun finding OPEN per la spec '<name>'.`
   - **Reviewer has zero OPEN findings**: `Nessun finding OPEN per '<Reviewer Name>' su spec abilitate.`
5. On success, jump to the fix loop (spec target → step 4a.2;
   reviewer target → step 4a-R.2).

### Refresh shortcut

Syntax: `/review-spec refresh <target>` where `<target>` is a
**spec name** or `all`.

#### Shortcut flow

1. Step 1 runs normally (data gathering via subagent).
2. Steps 2-3 are skipped (no recap table, no mode question).
3. Step 4e.1 is skipped (no spec selection question).
4. Validate the target against cached data in `.tracking.json`:
   - `all` (case-insensitive) → select all enabled specs with OPEN
     findings. If none: `Nessun finding OPEN su spec abilitate.`
   - Otherwise → treated as a spec name:
     - **Spec not found**: `Errore: la spec '<name>' non esiste in 'docs/features/'.`
     - **Spec disabled**: `Errore: la spec '<name>' è disabilitata. Abilitala prima con '/review-spec' → Toggle spec tracking.`
     - **Spec has zero OPEN findings**: `Nessun finding OPEN per la spec '<name>'.`
5. On success, jump to step 4e.2.

### Compact shortcut

Syntax: `/review-spec compact <target>` where `<target>` is a
**spec name** or `all`.

Migrates RESOLVED findings from the legacy verbose format (with
`**Category**`, separate `**Resolution**` line, and description body)
to the compact format (single `**Status**: RESOLVED — ...` line). Use
this to compact existing review files. All future resolution flows
(fix, auto-resolve, cross-agent dedup, refresh) already write compact
format natively, so this shortcut is only needed for legacy migration.

#### Shortcut flow

1. Step 1 runs normally (data gathering via subagent).
2. Steps 2-3 are skipped (no recap table, no mode question).
3. Validate the target against cached data in `.tracking.json`:
   - `all` (case-insensitive) → select all enabled specs with
     `cache.resolved > 0`. If none:
     `Nessun finding RESOLVED da compattare su spec abilitate.`
   - Otherwise → treated as a spec name:
     - **Spec not found**: `Errore: la spec '<name>' non esiste in 'docs/features/'.`
     - **Spec disabled**: `Errore: la spec '<name>' è disabilitata. Abilitala prima con '/review-spec' → Toggle spec tracking.`
     - **Spec has zero RESOLVED findings**: `Nessun finding RESOLVED da compattare per la spec '<name>'.`
4. Execute compaction.

For **single spec**: use Task tool (`general`). Instruct it to read:
- `.opencode/commands/review-spec/review-file-format.md`

The subagent MUST:

1. Read the review file (`docs/drafts/review/<name>.md`)
2. Identify all RESOLVED findings still in verbose format. A finding is
   in verbose format if it has any of: a `**Category**` line, a
   separate `**Resolution**` line, or a description body below the
   status/resolution lines
3. For each verbose RESOLVED finding, rewrite to compact format:
   - Keep the finding header exactly as-is:
     `### <ID> — <Title> (<Severity>)`
   - Merge status and resolution into a single line:
     `**Status**: RESOLVED — <resolution text> (<date>)`
   - Remove the `**Category**` line
   - Remove the original description body
4. Leave all OPEN findings and already-compact RESOLVED findings
   untouched
5. Write the updated review file
6. `.tracking.json` does NOT change (open/resolved counts are the same)
7. `README.md` does NOT change (open/resolved counts are the same)
8. Return: count of findings compacted, approximate line reduction

Pass to the subagent: spec name, review file path.

For **ALL**: launch one Task agent per spec **in parallel** (each
performing steps 1-8 above independently). After all return, present
the aggregated recap.

#### Recap

```
Compattazione completata:
  <spec-1>: N findings compattati
  <spec-2>: N findings compattati
  ...

Totale: N findings compattati su K spec
```

If a spec has no verbose RESOLVED findings (all already compact):
`<spec>: già compattata (nessun finding da convertire)`

---

## Step 1: Gather data (silent)

Use Task tool (subagent `explore`). Instruct it to:
1. Read `.opencode/commands/review-spec/tracking-format.md` for the
   schema and rules.
2. Find all `.md` files in `docs/features/` (Glob `docs/features/**/*.md`,
   exclude `docs/features/**/pages/*.md`). Use filename without extension
   as spec name. Sort alphabetically.
3. Read `docs/drafts/review/.tracking.json` (use
   `bash ls docs/drafts/review/` first). Handle init/sync/cleanup per
   the tracking format reference. Write to disk if changed.
4. Do NOT read or parse review files — trust the cache.
5. Return the full `.tracking.json` content.

---

## Step 2: Display recap

Show only **enabled** specs from `.tracking.json`, sorted alphabetically:

```
| Spec             | GAP | COH | DES | SEC | API | Total | Last Review |
|------------------|-----|-----|-----|-----|-----|-------|-------------|
| tickets          |   3 |   1 |   1 |   2 | 🟢  |     7 | 2025-01-15  |
|                  | 1:🔴 2:🟠 | 1:🟡 | 1:🟠 | 1:🔴 1:🟠 |  | 2:🔴 4:🟠 1:🟡 |  |
| **Total**        |   5 |   1 |   2 |   2 | 🟢  |    10 | —           |
```

Rendering rules (from `cache`):
- `cache: null` → all cells `—`, Last Review `—`, Total `(never)`
- Section in `not_reviewed` → cell `—`
- Section all H/M/L = 0 and NOT in `not_reviewed` → cell `🟢`
- Section H+M+L > 0 → cell shows total; sub-row shows severity breakdown

Edge cases:
- All caches null: `No review data found for enabled specs. You can run reviews to generate findings.`
- All specs disabled: `All specs are currently disabled. Use "Toggle spec tracking" to enable specs before running reviews.`

---

## Step 3: Ask mode

Use `question` tool. Show only applicable options:
- **Fix findings** — "Resolve open findings one at a time" (only if
  enabled specs have OPEN findings)
- **Run reviews** — "Run all reviewer agents on specs" (only if
  enabled specs exist)
- **Run single reviewer** — "Run one specific reviewer on one or all
  specs" (only if enabled specs exist)
- **Refresh findings** — "Revalidate open findings against current spec"
  (only if enabled specs have OPEN findings)
- **Toggle spec tracking** — "Enable or disable spec tracking"

---

## Step 4a: Fix findings flow

### 4a.0. Cross-spec patterns + grouping mode

Use Task tool (`general`) to read all review files of enabled specs
(use `bash ls docs/drafts/review/`), extract OPEN findings, and group
by category + similar keywords across specs. Return list of patterns.

If patterns found, present them:

```
Pattern comuni tra spec:
  - "Missing error path for concurrent updates" → tickets, package-tracking
  - "Unspecified pagination limits" → rbac, tickets
```

If no patterns found, skip this section (show nothing).

Then ask via `question` tool:
- **By spec** — "Work on all findings of a specific spec"
- **By reviewer** — "Work on all findings of a specific reviewer, across all specs"

### By-spec flow

**4a.1.** Ask via `question` tool: show enabled specs with OPEN findings,
sorted by total descending. Label = spec name, description = counts.

**4a.2.** Use Task tool (`general`) to read the review file and target
spec, parse all OPEN findings, sort by severity (H→M→L) then section
order (GAP→COH→DES→SEC→API). Return parsed findings only (not raw file
content).

**4a.3. Fix loop** — for the highest-priority OPEN finding:

**Mode management**: remain in Plan mode during analysis/presentation.
User switches to Build mode for fixes. After fix, do NOT present the
next finding in the same message.

**4a.3a.** Present the finding (in Italian). Apply Guardrail 21
placement self-check before proposing solution.

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
  - ...

Approvi questa soluzione? [sì / modificare / saltare / basta]
```

**4a.3b.** Wait for decision:
- **sì**: wait for Build mode, then implement
- **modificare**: adjust proposal, re-present
- **saltare**: skip, move to next (stay in Plan)
- **basta**: exit loop, show session summary

**4a.3c.** Use Task tool (`general`, fresh session) to implement the
fix. Instruct it to read:
- `.opencode/commands/review-spec/fix-procedure.md`
- `.opencode/commands/review-spec/readme-layout.md`

Pass: finding details, approved solution, file list, spec name/abbr.

**4a.3d.** Ask: `"Finding risolto. Continuo con il prossimo? (ricorda di passare a Plan con Tab prima di rispondere)"`
Do NOT present next finding in this message. Wait for reply.

### By-reviewer flow

**4a-R.1.** Ask via `question` tool: show only reviewers with at least
one OPEN finding across enabled specs. Single selection only. Options
in this order (skipping those with zero OPEN findings):

1. **Gap Analysis**
2. **Coherence**
3. **Design**
4. **Security**
5. **API Conventions**

Label = reviewer name, description = total open + affected spec count
(e.g., "9 open findings across 3 specs").

**4a-R.2.** Use Task tool (`general`) to read all review files of
enabled specs, extract OPEN findings for the chosen reviewer, sort by
severity then spec name. Return parsed findings (not raw content).

**4a-R.3. Fix loop** — same mode management as by-spec flow.

**4a-R.3a.** Use Task tool (`general`, new session per spec change) to
load the target spec + references + cross-cutting docs, apply Guardrail
21, and formulate the presentation (Contesto + Soluzione proposta in
Italian). Reuse session (`task_id`) for same-spec findings.

Present with added `Spec: <spec-name>` line under the finding header.

**4a-R.3b.** Wait for decision (same as 4a.3b).

**4a-R.3c.** Use a **FRESH** Task subagent (`general`) for
implementation — same as 4a.3c. Do NOT reuse the analysis session.

**4a-R.3d.** After fix, check if next finding is same or different spec:
- **Same spec**: `"Finding risolto. Continuo con il prossimo? (ricorda di passare a Plan con Tab prima di rispondere)"`
- **Different spec**: `"Finding risolto. Il prossimo finding è in '<next-spec>'. Carico il contesto della nuova spec (sessione fresca). Continuo? (ricorda di passare a Plan con Tab prima di rispondere)"`

If user says yes and spec changes, start fresh Task session. If no,
show session summary grouped by spec. Do NOT present next finding in
this message.

---

## Step 4b: Run reviews flow

**4b.1.** Ask via `question` tool. Single selection only. Options:

1. **ALL** — "Run review on all enabled specs sequentially"
2. Then each enabled spec alphabetically, with description:
   - If previously reviewed: "Last review: <date> — <N> open findings"
   - If never reviewed: "Never reviewed"

**4b.2.** Execute review using the procedure in
`.opencode/commands/review-spec/review-procedure.md` (section "Full
review"). Launch 5 reviewer Task agents in parallel per spec.

After reviewers return, use Task tool (`general`) to assemble results.
Instruct it to read:
- `.opencode/commands/review-spec/review-file-format.md`
- `.opencode/commands/review-spec/readme-layout.md`

**4b.3.** Final report: specs reviewed, total findings by severity,
which specs have High-severity findings.

---

## Step 4c: Run single reviewer flow

**4c.1.** Ask which reviewer via `question` tool. Single selection
only. Options in this order:

1. **Gap Analysis** — "Uncovered functional cases, missing state
   transitions, error paths, boundary conditions"
2. **Coherence** — "Contradictions and terminology inconsistencies
   with other specs"
3. **Design** — "Architectural decisions, complexity, edge cases,
   alternatives, maintainability"
4. **Security** — "Security vulnerabilities, insecure patterns,
   missing controls"
5. **API Conventions** — "API endpoint definitions conformity
   (error codes, naming, pagination, envelope)"

**4c.2.** Ask which spec via `question` tool. Single selection only.
Options:

1. **ALL** — "Run reviewer on all enabled specs in parallel"
2. Then each enabled spec alphabetically, with description:
   - If previously reviewed: "Last review: <date> — <N> open findings"
   - If never reviewed: "Never reviewed"

**4c.3.** Execute using the procedure in
`.opencode/commands/review-spec/review-procedure.md` (section "Single
reviewer"). If ALL, launch one Task agent per spec in parallel.

After reviewer(s) return, use Task tool (`general`) to write/update
files. Instruct it to read:
- `.opencode/commands/review-spec/review-file-format.md`
- `.opencode/commands/review-spec/readme-layout.md`

**4c.4.** Final report: reviewer executed, specs processed, findings
by severity, High-severity specs.

---

## Step 4d: Toggle spec tracking

**4d.1.** Use `question` tool with `multiple: true`. Show ALL specs
(enabled + disabled), sorted alphabetically. Description shows current
state and context (e.g., "Currently: ENABLED — 5 open findings").

**4d.2.** Use Task tool (`general`) to apply toggles. Instruct it to
read:
- `.opencode/commands/review-spec/tracking-format.md`
- `.opencode/commands/review-spec/readme-layout.md`

**Enabling** (disabled → enabled): if cache has OPEN findings, read
spec + review file, validate each OPEN finding (auto-resolve if no
longer applicable using the compact RESOLVED format:
`**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (<YYYY-MM-DD>)`).
If no OPEN findings, just flip `enabled`.

**Disabling** (enabled → disabled): flip `enabled` to `false`. Leave
review file and cache untouched (frozen).

After all toggles: write `.tracking.json`, update README.

**4d.3.** Present results:
```
Tracking aggiornato:
  ✓ pages: DISABLED → ENABLED (3 findings validated, 1 auto-resolved)
  ✓ references: ENABLED → DISABLED (2 findings frozen)
```

---

## Step 4e: Refresh findings flow

Re-evaluates all OPEN findings for selected spec(s) against the current
spec content. Findings that are no longer applicable are auto-resolved.
Cross-agent duplicates (OPEN findings matching RESOLVED findings in
other sections) are also resolved.

**4e.1.** Ask which spec via `question` tool. Single selection only.
Options:

1. **ALL** — "Refresh all enabled specs with open findings"
2. Then each enabled spec with OPEN findings, sorted by open count
   descending. Label = spec name, description = open findings count
   (e.g., "15 open findings")

**4e.2.** Execute refresh.

For **single spec**: use Task tool (`general`). Instruct it to read:
- `.opencode/commands/review-spec/review-file-format.md`
- `.opencode/commands/review-spec/review-procedure.md` (section
  "Cross-agent deduplication" — for semantic equivalence criteria)
- `.opencode/commands/review-spec/readme-layout.md`

The subagent MUST:

1. Read the target spec (`docs/features/**/<name>.md`)
2. Read all specs referenced by the target (follow links and
   cross-references)
3. Read cross-cutting documents: `docs/data-model.md`,
   `docs/api-spec.md`, `docs/architecture.md`
4. Read the review file (`docs/drafts/review/<name>.md`)
5. Collect ALL RESOLVED findings from the review file (across all
   sections) — needed for cross-agent deduplication
6. For each OPEN finding, perform two checks:
   - **Spec validity**: is the issue described in the finding still
     present in the current spec? If the spec has been changed such
     that the finding is no longer applicable (the section was
     rewritten, the missing element was added, the contradiction was
     resolved), the finding should be auto-resolved
   - **Cross-agent duplicate**: is this OPEN finding semantically
     equivalent to a RESOLVED finding in a **different** section?
     (use the semantic equivalence criteria from
     `review-procedure.md`)
7. For each finding, return a verdict:
   - `still_valid` — the issue is still present
   - `auto_resolved` — the spec has changed and the issue no longer
     applies. Include a brief reason
   - `cross_agent_duplicate` — matches a RESOLVED finding in another
     section. Include the ID of the matched finding
8. Update the review file using the compact RESOLVED format (see
   `review-file-format.md`):
   - Auto-resolved findings:
     `**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (<YYYY-MM-DD>)`
   - Cross-agent duplicates:
     `**Status**: RESOLVED — Cross-agent duplicate of <ORIGINAL_ID> (<YYYY-MM-DD>)`
   - Remove `**Category**` line and description body for each resolved
     finding
   - Preserve finding order and all other content unchanged
9. Update `.tracking.json` cache (recalculate open/resolved counts)
10. Update `docs/drafts/review/README.md`
11. Return: per-finding verdicts + updated counts

Pass to the subagent: spec name, abbreviation, today's date, paths to
spec and review file. Do NOT pass raw spec content in the prompt.

For **ALL**: launch one Task agent per spec **in parallel** (each
performing steps 1-8 above independently and returning verdicts). After
all return, use a single Task tool (`general`) to:

1. Update `.tracking.json` cache for all refreshed specs
2. Update `docs/drafts/review/README.md`
3. Return: aggregated per-spec summaries

**4e.3.** Present recap.

Single spec:
```
Refresh completato per '<spec-name>':
  ✓ <ID> — <Title>: auto-resolved (<brief reason>)
  ✓ <ID> — <Title>: cross-agent duplicate of <OTHER_ID>
  — <ID> — <Title>: still valid
  — <ID> — <Title>: still valid

Totale: N findings rivalutati, X auto-resolved, Y still valid
```

Multiple specs (ALL):
```
Refresh completato:
  <spec-1>: N rivalutati, X auto-resolved, Y still valid
  <spec-2>: N rivalutati, X auto-resolved, Y still valid
  ...

Totale: N findings rivalutati su K spec, X auto-resolved
```

If no findings were auto-resolved across all specs:
```
Refresh completato: tutti i N findings sono ancora validi.
```
