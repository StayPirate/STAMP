# Configuration Reference Restructuring Plan

## Objective

Restructure `docs/configuration.md` to improve consistency, navigability,
and logical grouping. Fix content accuracy issues identified during review.

## Principles

- **No content loss**: every piece of information in the current file must
  be preserved. Each phase ends with a diff verification
- **Structural changes**: column uniformity, heading levels, section
  ordering, and internal sub-sectioning
- **Accuracy fixes**: correct documented behavior to match feature specs
  (source of truth), reduce verbatim duplication, and unify type notation
- **Authority chain**: `configuration.md` is a mirror of feature specs —
  it must not introduce information absent from the owning spec, and must
  not contradict the owning spec. Any type/behavior change applied to
  `configuration.md` MUST be verified against (and, if needed, applied
  to) the owning feature spec in the same phase
- **Document the exception, not the default**: a constraint description
  states additional behavior only when that behavior deviates from what
  the reader would reasonably assume. "Must be >= 1" already implies a
  hard rejection of invalid values by default — this is only restated
  when the actual behavior is a graceful fallback (the surprising case).
  Restating the hard-fail default adds no information and is
  over-documentation

## Current Issues

### Structural

1. Inconsistent table columns (4 vs 5 columns, `Variable` vs `Env Var`)
2. Misplaced heading level (`###` under Logging for unrelated content)
3. No clear ordering criterion for sections
4. Dense prose in Celery section with no sub-structure
5. Inconsistent section granularity (IBS split in two; SMELT/AIMAAS merged)

### Content accuracy — confirmed, to fix

6. **Finding A**: SSO "enabled" startup log message exists only in
   `configuration.md` (lines 118-119) — not in the owning feature spec
   `sso-authentication.md`. This inverts the authority chain
7. **Finding B**: Celery startup validation (lines 54-66) duplicated
   nearly verbatim from `fetcher-infrastructure.md` (lines 2381-2394).
   The detail level (exact code paths, RuntimeError string) exceeds what
   a configuration reference needs
8. **Finding D**: `LOGIN_MAX_ATTEMPTS` and `LOGIN_LOCKOUT_MINUTES`
   descriptions say "Must be >= 1", implying hard failure. The owning
   spec (`local-authentication.md:298-301`) specifies graceful fallback
   to defaults with a warning. An operator reading only `configuration.md`
   would expect the app to refuse to start. This is a genuine deviation
   from the reasonable default expectation, and therefore must be stated
9. **Finding 3**: `CORS_ORIGINS` is documented as
   `list (comma-separated)`, but `config.py:39` (`cors_origins:
   list[str]`) and `.env.example:17`
   (`CORS_ORIGINS=["http://localhost:5173"]`) confirm Pydantic Settings
   parses it as a **JSON array**. A comma-separated value (`a,b`) would
   fail to parse. This is a real correctness bug in the documented type,
   not a cosmetic inconsistency

### Content accuracy — evaluated and discarded

The following candidate fixes were proposed in earlier review rounds and
discarded after verification. They are recorded here to prevent
re-introduction in a future round.

10. **Discarded — "Finding E" / IBS routing keys type change**: an
    earlier round proposed changing `IBS_RABBITMQ_ROUTING_KEYS` from
    `string` to `list (comma-separated)` to "match" `CORS_ORIGINS`'s
    notation. This was based on a false premise: the two variables use
    genuinely different input formats.
    `CORS_ORIGINS` is parsed by Pydantic as a JSON array (see Finding 3);
    `IBS_RABBITMQ_ROUTING_KEYS` is a plain string split manually by
    application code on commas — it is not a Pydantic `list[str]` field.
    `configuration.md:157` and `ibs-rabbitmq-integration.md:437` already
    **agree** with each other (both say `string` with "Comma-separated"
    in the description) — there is no authority-chain divergence to fix.
    Relabeling it `list (comma-separated)` would be actively misleading,
    implying JSON-array parsing semantics it does not have. **No change
    needed.**
11. **Discarded — cross-file update to `ibs-rabbitmq-integration.md`
    (dependent on item 10)**: a follow-up finding proposed updating
    `ibs-rabbitmq-integration.md:437` to keep it "in sync" with the
    `configuration.md` change from item 10. Since item 10 itself is
    discarded, this dependent fix is also discarded. No change to
    `ibs-rabbitmq-integration.md` is needed for this restructuring.
12. **Discarded — JWT/session hard-failure restatement**: a follow-up
    finding proposed adding "(application refuses to start on invalid
    values)" to the `JWT_EXPIRY_HOURS` and `SESSION_MAX_LIFETIME_DAYS`
    descriptions in `configuration.md`, arguing that the contrast with
    Finding D's fallback wording made the omission more visible. Rejected
    per the "document the exception, not the default" principle above:
    `authentication.md:62-74` confirms JWT/session settings **do** hard
    fail on invalid values, which is exactly what "Must be >= 1" already
    conveys by default. Unlike `LOGIN_*` (which deviates from that
    default via graceful fallback — Finding D), JWT/session settings
    conform to it. Restating conforming behavior is over-documentation.
    **No change needed.**

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

Note: the "Startup Validation" sub-section from the original plan has
been removed from the outline. In Phase 4, the startup validation prose
is replaced by a one-sentence cross-reference to
`fetcher-infrastructure.md` (Finding B fix), which is short enough to
remain inline under "Timezone Enforcement" without its own sub-heading.

## Phases

### Phase 1 — Fix heading level

**Scope**: Change `### Standard Environment Variables (Non-Sentinel)`
from H3 to H2, removing its false nesting under "Logging".

**Changes**: Single line change in `docs/configuration.md`.

**Verification**: diff shows only heading marker change (`###` → `##`).

---

### Phase 2 — Uniform table columns and type notation

**Scope**:
- "Required Secrets" table: add `Default` column with value
  `— (required)` to match the 5-column format used elsewhere
- "Standard Environment Variables" table: rename `Variable` column to
  `Env Var`
- **Finding 3 fix**: change `CORS_ORIGINS` type in `configuration.md`
  from `list (comma-separated)` to `list (JSON array)`, matching the
  actual Pydantic Settings parsing behavior (`cors_origins: list[str]`
  in `config.py`) and the `.env.example` syntax
  (`CORS_ORIGINS=["http://localhost:5173"]`)
- **No change to `IBS_RABBITMQ_ROUTING_KEYS`** — see "Content accuracy
  — evaluated and discarded" (items 10-11) above. It remains `string`
  with "Comma-separated" in the description, in both `configuration.md`
  and `ibs-rabbitmq-integration.md` (already consistent)

**Changes**: Table modifications in `docs/configuration.md` only.

**Verification**: diff shows only column header additions/renames and
the `CORS_ORIGINS` type correction. No other row content changes.
Confirm `IBS_RABBITMQ_ROUTING_KEYS` is untouched in both
`configuration.md` and `ibs-rabbitmq-integration.md`.

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
changed. Line count remains identical. Content grep for every env var
name confirms no loss.

---

### Phase 4 — Sub-section Celery prose + Finding B fix

**Scope**: Introduce `###` sub-headings within the "Celery Worker
Configuration" section to organize the existing prose into navigable
units. Simultaneously, replace the verbatim-duplicated startup
validation prose with a brief cross-reference.

**Sub-sections**:
- `### Timezone Enforcement` — wraps the "fixed" paragraph. The
  detailed startup validation prose (code paths, RuntimeError string)
  is replaced by a one-sentence summary with cross-reference:
  "The Celery app factory validates these values at import time and
  refuses to start if either is overridden — see
  `docs/features/platform/fetcher-infrastructure.md` (Startup
  Validation) for the exact validation logic."
- `### Result Handling` — wraps the `task_ignore_result` paragraph
- `### Redbeat Scheduler` — wraps the redbeat paragraph
- `### Beat Tick Interval` — wraps the `beat_max_loop_interval`
  paragraph
- `### retry_period (redbeat)` — wraps the `retry_period` paragraph

**Finding B fix detail**: the current text (lines 54-66) reproduces
the validation check code (`app.conf.timezone != "UTC"`), the exact
RuntimeError message string, and the import-time execution detail —
all of which are implementation specifics already specified in
`fetcher-infrastructure.md:2381-2394`. The replacement retains the
operational fact (app refuses to start) while eliminating the
duplicated implementation detail.

**Verification**: diff shows inserted heading lines and the replaced
startup validation paragraph. All other prose text appears unchanged.

---

### Phase 5 — Fix content accuracy (Findings A and D)

**Scope**: Two content accuracy fixes that correct the authority chain
and align `configuration.md` with the owning feature specs.

**Finding A fix** (SSO "enabled" log message):
- Add the "enabled" startup log message to `sso-authentication.md`
  (the source of truth). The message already in `configuration.md`
  is: `"SSO authentication enabled (issuer: {SSO_ISSUER_URL})"`.
  Add it to the SSO configuration section of `sso-authentication.md`
  alongside the existing "disabled" message (line 38-40), so both
  startup log messages are specified in the feature spec
- `configuration.md` already has the message — no change needed there

**Finding D fix** (login lockout fallback behavior):
- Update the `LOGIN_MAX_ATTEMPTS` description in `configuration.md`
  from "Must be >= 1" to "Must be >= 1; values below minimum fall
  back to default with a startup warning"
- Apply the same change to `LOGIN_LOCKOUT_MINUTES`
- This aligns `configuration.md` with the behavior specified in
  `local-authentication.md:298-301`
- **No corresponding change to `JWT_EXPIRY_HOURS` or
  `SESSION_MAX_LIFETIME_DAYS`** — see "Content accuracy — evaluated and
  discarded" (item 12) above. Their existing "Must be >= 1" wording
  already correctly conveys hard-fail behavior; that is the
  default-conforming case, not the exception

**Verification**: diff shows one addition in `sso-authentication.md`
and two description changes in `configuration.md`
(`LOGIN_MAX_ATTEMPTS`, `LOGIN_LOCKOUT_MINUTES`). Confirm
`JWT_EXPIRY_HOURS` and `SESSION_MAX_LIFETIME_DAYS` descriptions are
unchanged. Cross-check that the wording matches the owning feature
specs exactly.

---

### Phase 6 — Verify and fix anchor links

**Scope**: Full-project search for any broken references to
`docs/configuration.md` (with or without anchors) and to the modified
section of `sso-authentication.md`. Since no section was renamed, this
phase is expected to be a no-op verification.

**Verification**: grep for `configuration.md` and
`sso-authentication.md` across the project. Confirm all textual
section references still match actual heading text.

---

### Phase 7 — Run reviewers

Invoke the following reviewers on the final state:
- `@docs-reviewer` on `docs/configuration.md`
- `@docs-placement-reviewer` on `docs/configuration.md`
- `@spec-coherence-reviewer` on `docs/configuration.md`
- `@spec-coherence-reviewer` on `docs/features/identity/sso-authentication.md`
  (due to Finding A addition)

Address any issues rated "Needs revision" before proceeding.

---

### Phase 8 — Delete this draft

Remove `docs/drafts/configuration-restructure.md` and commit.

## Risk Assessment

| Phase | Risk | Mitigation |
|-------|------|-----------|
| 1 | Minimal | Single character change |
| 2 | Low | Column additions and one type fix; row content otherwise unchanged |
| 3 | Medium | Block reordering; verified by line count + env var grep |
| 4 | Medium | Heading insertions + one paragraph replacement; verified by diff |
| 5 | Low | Two small content fixes aligned with owning specs |
| 6 | Low | Verification pass; likely no-op |
| 7 | None | Read-only review |
| 8 | None | Cleanup |
