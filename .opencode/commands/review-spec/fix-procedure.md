# Fix Procedure Reference

This document defines the procedure a Task subagent must follow when
implementing an approved fix for a review finding.

## Overview

CRITICAL: You MUST NOT mark a finding as RESOLVED without implementing
the approved solution in the actual spec/documentation files. Marking a
finding as RESOLVED without applying the fix is a bug. The
implementation IS the fix — the review file update is merely a record
of it.

All four operations below MUST be performed in a single subagent
session, in this order.

## Step 1: Implement the approved solution

Read and edit all files listed in "File coinvolti". Verify every listed
file is modified. If a listed file turns out not to need changes, skip
it but include the reason in the confirmation.

## Step 2: Update the review file

Mark the finding as RESOLVED in `docs/reviews/<name>.md` using
the compact format (see `review-file-format.md`):

```markdown
### <ID> — <Title> (<Severity>)

**Status**: RESOLVED — <Short description of what was changed and where> (<YYYY-MM-DD>)
```

Remove the `**Category**` line and the original detailed description.
Only the finding header (ID, title, severity) and the single-line
status with resolution text are kept.

## Step 3: Update README index

Update `docs/reviews/README.md` following the rules in
`.opencode/commands/review-spec/readme-layout.md`.

## Step 4: Update cache in `.tracking.json`

Recalculate the `cache` field for this spec:
- Decrement the OPEN count for the resolved finding's section and
  severity
- Increment the `resolved` count
- Write the updated `.tracking.json` to disk

## Execution order

CRITICAL: implement the fix FIRST (Step 1), then update the review
file and cache (Steps 2-4). If the fix fails for any reason, the
finding MUST remain OPEN and the cache MUST NOT be updated.

## Input (passed by the orchestrator)

The orchestrator passes:
- The finding ID, title, severity, category, full description
- The approved solution (from the presentation)
- The list of files to modify and what to change in each
- The spec name, abbreviation, and review file path

## Output (returned to the orchestrator)

Return a brief confirmation:
- List of files modified (path + one-line summary of change)
- New open/resolved counts for the review file
- Any file that was NOT modified and why
- **Change classification**: one or more tags from the closed vocabulary
  below, describing the nature of the changes applied to the spec. The
  orchestrator uses these tags to decide whether to recommend re-running
  reviewer agents after the fix.

### Change classification tags

| Tag | When it applies |
|-----|-----------------|
| `api-endpoint-changed` | API endpoint added, modified, or removed |
| `business-rule-changed` | Business rule, state transition, data flow, or operation added/modified |
| `error-path-changed` | Error path or edge case added/modified |
| `auth-changed` | Authentication, authorization, or RBAC rules modified |
| `cross-ref-changed` | Cross-spec reference added/modified |
| `terminology-changed` | Status, enum, or concept renamed; new term introduced |
| `config-changed` | Environment variable, default value, or configuration parameter added/changed |
| `rule-or-pattern-added` | New reusable rule, convention, or pattern added |
| `design-changed` | Architectural decision or design approach changed |
| `structural-rewrite` | Significant rewrite of a section |
| `cosmetic` | Formatting, typo, or clarification with no semantic change |

A fix typically produces 1-3 tags. Use `cosmetic` only when no other
tag applies. Use `structural-rewrite` when a section is substantially
rewritten (not just a sentence added/changed).
