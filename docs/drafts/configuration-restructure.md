# Configuration Reference Restructuring Plan

## Objective

Restructure `docs/configuration.md` to improve consistency, navigability,
and logical grouping — without modifying the informational content.

## Principles

- **No content loss**: every piece of information in the current file must
  be preserved. Each phase ends with a diff verification
- **No semantic changes**: defaults, types, descriptions, and "Defined in"
  links remain identical
- **Structure only**: column uniformity, heading levels, section ordering,
  and internal sub-sectioning

## Current Issues

1. Inconsistent table columns (4 vs 5 columns, `Variable` vs `Env Var`)
2. Misplaced heading level (`###` under Logging for unrelated content)
3. No clear ordering criterion for sections
4. Dense prose in Celery section with no sub-structure
5. Inconsistent section granularity (IBS split in two; SMELT/AIMAAS merged)

## Target Section Order

After restructuring, the document will follow this logical grouping:

```
# Configuration Reference
  (intro paragraph — unchanged)

## Required Secrets
## Required Connection Settings
## Application
## Logging
## Authentication
## SSO Configuration
## Celery Worker Configuration
  ### Timezone Enforcement
  ### Startup Validation
  ### Result Handling
  ### Redbeat Scheduler
  ### Beat Tick Interval
  ### retry_period (redbeat)
## IBS (Internal Build Service)
## IBS RabbitMQ Consumer
## TLS / Security
## SMELT / AIMAAS
## External APIs
## Git-Based Fetchers
## Standard Environment Variables (Non-Sentinel)
## Runtime Database Settings
## Notes for Operators
```

Rationale:
- Core infrastructure first (secrets, connections, app identity, logging)
- Identity/auth cluster (authentication, SSO)
- Task queue (Celery) — standalone due to its complexity
- External integrations by specificity (IBS, IBS RabbitMQ, TLS, SMELT,
  APIs, Git fetchers)
- Non-Sentinel variables — clearly separated at the end
- Non-env-var settings (Runtime Database) before closing notes
- Notes for Operators as closing section (unchanged)

## Phases

### Phase 1 — Fix heading level

**Scope**: Change `### Standard Environment Variables (Non-Sentinel)`
from H3 to H2, removing its false nesting under "Logging".

**Changes**: Single line change in `docs/configuration.md`.

**Verification**: diff shows only heading marker change (`###` → `##`).

---

### Phase 2 — Uniform table columns

**Scope**:
- "Required Secrets" table: add `Default` column with value `—
  (required)` to match the 5-column format used elsewhere
- "Standard Environment Variables" table: rename `Variable` column to
  `Env Var`

**Changes**: Two table modifications in `docs/configuration.md`.

**Verification**: diff shows only column header additions/renames and
cell additions. No row content changes.

---

### Phase 3 — Reorder sections

**Scope**: Move sections to match the Target Section Order above.
No text is added or removed — only block-level reordering.

**Changes**:
- Move "Application" and "Logging" after "Required Connection Settings"
- Move "Authentication" and "SSO Configuration" after "Logging"
- Move "Standard Environment Variables" to its own H2 position after
  "Git-Based Fetchers" (already fixed in Phase 1)

**Prose references to update** (section names cited in other files):
- `docs/deployment.md` line 474: "Celery Worker Configuration" — no
  rename, no update needed
- `docs/conventions.md` line 183: "Celery Worker Configuration" — no
  rename, no update needed
- `docs/features/platform/fetcher-infrastructure.md` line 1484: "Celery
  Worker Configuration" — no rename, no update needed

Since no section is being renamed in this phase, only reordered, the
prose references remain valid.

**Verification**: `diff --stat` shows only `docs/configuration.md`
changed. Line count remains identical (250 lines). Content grep for
every env var name confirms no loss.

---

### Phase 4 — Sub-section Celery prose

**Scope**: Introduce `###` sub-headings within the "Celery Worker
Configuration" section to organize the existing prose into navigable
units. No text is rewritten — only headings are inserted between
existing paragraphs.

**Sub-sections**:
- `### Timezone Enforcement` — wraps the "fixed" paragraph + startup
  validation paragraph
- `### Result Handling` — wraps the `task_ignore_result` paragraph
- `### Redbeat Scheduler` — wraps the redbeat paragraph
- `### Beat Tick Interval` — wraps the beat_max_loop_interval paragraph
- `### retry_period` — wraps the retry_period paragraph

**Verification**: diff shows only inserted heading lines (`###`). All
original prose text appears unchanged in the diff context.

---

### Phase 5 — Verify and fix anchor links

**Scope**: Full-project search for any broken references to
`docs/configuration.md` (with or without anchors). Since no section
was renamed, this phase is expected to be a no-op verification.

**Verification**: grep for `configuration.md` across the project.
Confirm all textual section references still match actual heading text.

---

### Phase 6 — Run reviewers

Invoke the following reviewers on the final state:
- `@docs-reviewer` on `docs/configuration.md`
- `@docs-placement-reviewer` on `docs/configuration.md`
- `@spec-coherence-reviewer` on `docs/configuration.md`

Address any issues rated "Needs revision" before proceeding.

---

### Phase 7 — Delete this draft

Remove `docs/drafts/configuration-restructure.md` and commit.

## Risk Assessment

| Phase | Risk | Mitigation |
|-------|------|-----------|
| 1 | Minimal | Single character change |
| 2 | Low | Column additions only; row content unchanged |
| 3 | Medium | Block reordering; verified by line count + env var grep |
| 4 | Low | Heading insertions only; prose unchanged |
| 5 | Low | Verification pass; likely no-op |
| 6 | None | Read-only review |
| 7 | None | Cleanup |
