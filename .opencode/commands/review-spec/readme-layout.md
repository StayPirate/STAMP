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
  review date, stale indicator. Use `—` for sections that have never
  been reviewed (`_Not yet reviewed._` in the review file). Use `🟢`
  for sections that have been reviewed but have zero OPEN findings
  (either no findings were raised, or all findings are RESOLVED).
- **Sub-row**: severity breakdown per section using colored circles:
  `🔴` = High, `🟠` = Medium, `🟡` = Low. Format: `N:🔴 N:🟠 N:🟡`,
  separated by spaces, omitting severities with zero count. Leave cell
  empty if the main row is `—` or `🟢`. Stale sub-row cell is always
  empty.
- **Total row** at the bottom: sum of OPEN findings across **enabled
  specs only**, with severity sub-row. Stale cell is empty.

## Disabled section

Shown below the main table, preceded by a `### Disabled specs` heading.
Simple bullet list of spec names, one per line, sorted alphabetically:

```markdown
### Disabled specs

- spec-name-a
- spec-name-b
- spec-name-c
```

No table, no columns, no severity breakdowns. Just names.

The cache in `.tracking.json` is preserved when a spec is disabled
(not cleared). When a spec is re-enabled, its cached data is used to
populate the main table immediately.

## Stale column

The **Stale** column indicates whether the spec file has been modified
after the last review date, signaling the review may be outdated.

Computation (at README generation time):

1. For each spec, get the last commit date of the spec file:
   `git log -1 --format='%Y-%m-%d' -- docs/features/**/<spec-name>.md`
2. Compare with `cache.date` from `.tracking.json`:
   - `git_date > cache.date` → `⚠️`
   - `git_date <= cache.date` → cell empty (review is current)
   - `cache` is `null` (never reviewed) → `—`

The Stale cell in sub-rows and the Total row is always empty.

## When to update

The README index is updated:
- After writing/modifying any findings file (fix implementation,
  review execution)
- After toggling spec tracking
- Always recalculate the Total row based on current enabled specs
