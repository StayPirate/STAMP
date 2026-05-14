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
