# Review File Format Reference

This document defines the structure and format of review files written
to `docs/drafts/review/<spec-name>.md` by the `/review-spec` command.

## Full review file structure

```markdown
# Review: <spec-name>

**Spec**: `docs/features/**/<name>.md`
**Last reviewed**: <YYYY-MM-DD>
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### <ABBR>-GAP-01 — <Title> (High)

**Category**: <category>
**Status**: OPEN

<Detailed description of the finding>

### <ABBR>-GAP-02 — <Title> (Medium)

...

---

## Coherence

### <ABBR>-COH-01 — <Title> (Medium)

...

---

## Design

### <ABBR>-DES-01 — <Title> (Medium)

...

---

## Security

### <ABBR>-SEC-01 — <Title> (High)

...

---

## API Conventions

### <ABBR>-API-01 — <Title> (High)

...
```

## Section-to-prefix mapping

| Section | Prefix |
|---------|--------|
| Gap Analysis | GAP |
| Coherence | COH |
| Design | DES |
| Security | SEC |
| API Conventions | API |

## Finding format — OPEN

```markdown
### <ABBR>-<PREFIX>-<NN> — <Title> (<Severity>)

**Category**: <category>
**Status**: OPEN

<Detailed description of the finding>
```

## Finding format — RESOLVED

```markdown
### <ABBR>-<PREFIX>-<NN> — <Title> (<Severity>)

**Category**: <category>
**Status**: RESOLVED
**Resolution**: <Short description of what was changed and where> (<YYYY-MM-DD>)

<Original detailed description of the finding>
```

## New file skeleton

When a review file does not yet exist and only a single reviewer is
being executed, create the file with this skeleton and populate only
the section corresponding to the executed reviewer:

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

Replace the placeholder (`_Not yet reviewed._`) in the section
corresponding to the executed reviewer with the actual findings.

## Partial update rules (single reviewer)

When updating an existing review file with results from a single
reviewer:

1. Replace **only** the section (`## <Section Name>`) corresponding to
   the executed reviewer with the new findings; preserve all other
   sections untouched
2. Update file headers:
   - Set `**Last reviewed**` to today's date
   - Ensure the `**Reviewers**` line lists all sections that have been
     populated (not placeholders)

## Writing rules

- Use the `abbr` field from `.tracking.json` for finding IDs (e.g.,
  `TKT-GAP-01`)
- Within each section, sort findings by severity: High first, then
  Medium, then Low
- Each finding MUST have enough detail for the user to understand and
  act on it without re-running the reviewer
