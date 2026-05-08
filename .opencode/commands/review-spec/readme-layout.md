# README Index Layout Reference

This document defines the canonical layout rules for
`docs/drafts/review/README.md`. All steps that update the README index
MUST follow these rules.

## Structure

The README has two sections:
1. **Main table** — enabled specs only
2. **Disabled section** — disabled specs (shown below the main table)

## Main table (enabled specs)

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

## Disabled section

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

## When to update

The README index is updated:
- After writing/modifying any findings file (fix implementation,
  review execution)
- After toggling spec tracking
- Always recalculate the Total row based on current enabled specs
