# Review Procedure Reference

This document defines how reviewer agents are executed and how their
results are assembled into review files.

## Reviewer agent mapping

| Reviewer | Subagent type | Section prefix |
|----------|---------------|----------------|
| Gap Analysis | `spec-gap-analyzer` | GAP |
| Coherence | `spec-coherence-reviewer` | COH |
| Design | `design-reviewer` | DES |
| Security | `security-reviewer` | SEC |
| API Conventions | `api-convention-reviewer` | API |

## What each reviewer agent does

Each reviewer agent independently:

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

## RESOLVED finding regression check

Each reviewer MUST also check previously RESOLVED findings in its
section: if the resolution is still valid in the current spec, keep
RESOLVED; if the spec has regressed (the section modified by the
resolution was reverted or the content reintroduces the original
problem), reopen as OPEN. Cosmetic rewording or structural
reorganization of a correct fix does NOT constitute regression.

## Full review (all 5 reviewers)

Launch **5 Task agents in parallel** (one per reviewer, all in a single
message with multiple Task tool calls). After all return, use a separate
Task agent (subagent type `general`) to assemble results:

1. Assemble and write (or overwrite) `docs/drafts/review/<name>.md`
   using the findings from all 5 reviewers (see
   `.opencode/commands/review-spec/review-file-format.md` for the file
   structure)
2. Use the `abbr` field from `.tracking.json` for finding IDs
3. Update `docs/drafts/review/README.md` (see
   `.opencode/commands/review-spec/readme-layout.md`)
4. Recalculate and update the `cache` field for this spec in
   `.tracking.json`
5. Return: summary (findings per section, per severity, total open,
   total resolved)

Pass to the assembly subagent: the 5 sets of structured findings +
spec name + abbreviation + today's date + path to the spec file (for
the header). Do NOT pass raw spec content.

## Single reviewer

For a single reviewer run, launch one Task agent per spec (if ALL was
selected, launch in parallel — multiple Task tool calls in a single
message). After the reviewer Task agents return, use a separate Task
agent (subagent type `general`) to process ALL reviewed specs in a
single session:

1. For each reviewed spec, read the existing review file (if any)
2. Replace **only** the section corresponding to the executed reviewer
   with the new findings; preserve all other sections untouched
3. If no review file exists, create it with the standard skeleton and
   populate the reviewed section (see
   `.opencode/commands/review-spec/review-file-format.md`)
4. Update file headers (last reviewed date, reviewers list)
5. Use the `abbr` field from `.tracking.json` for finding IDs
6. Update `docs/drafts/review/README.md` (see
   `.opencode/commands/review-spec/readme-layout.md`)
7. Recalculate and update the `cache` field for each reviewed spec in
   `.tracking.json`
8. Return: per-spec summary (findings count, severities)

Pass to the subagent: all findings grouped by spec + spec names +
abbreviations + reviewer name + today's date. Do NOT pass raw spec
content.
