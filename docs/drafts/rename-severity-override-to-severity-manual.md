# Draft: Rename `severity_override` to `severity_manual`

## Motivation

The field `Ticket.severity_override` stores the VA's manually-set severity
for tickets without an associated CVE (`cve_id IS NULL`). The name
"override" is misleading because:

1. When `cve_id IS NULL`, the field is the **only** source of severity —
   it overrides nothing
2. When `cve_id IS NOT NULL`, the field cannot be set
   (`SeverityDerivedError`) — the override concept does not apply
3. The name suggests precedence over CVSS-derived severity, which is a
   non-existent scenario in the current design

The new name `severity_manual` accurately describes the field's semantics:
it is the manually-set severity value. Combined with the existing column
naming pattern (`concept_qualifier`: `delivery_status`, `cve_id`,
`assignee_id`), the name is both precise and consistent.

## Scope

This is a **spec-only** rename. No implementation code, database
migrations, or tests exist for this field. All changes are confined to
documentation and specification files.

## Rename Mapping

| Old identifier | New identifier | Context |
|---|---|---|
| `severity_override` (column) | `severity_manual` | Data model, ERD, schema tables |
| `set_severity_override()` (function) | `set_severity_manual()` | Service module function |
| `### severity_override Field` (section header) | `### severity_manual Field` | tickets.md |
| `### Set Severity Override` (endpoint header) | `### Set Severity Manual` | tickets.md |
| `#set-severity-override` (URL anchor) | `#set-severity-manual` | rbac.md endpoint permission map |
| `"severity_override"` (JSON body key in api-spec.md example) | `"severity"` (fix to match actual endpoint spec) | api-spec.md Mutation Patterns example |
| "severity override" (prose) | "manual severity" (prose) | Descriptions, capability text |

## Excluded Files

The following files reference `severity_override` but are **not modified**
because they are historical review findings (point-in-time snapshots):

- `docs/reviews/tickets.md`
- `docs/reviews/ticket-mutations.md`
- `docs/reviews/ticket-service.md`
- `docs/reviews/maintainer.md`

## Action Plan

### Step 1 — `docs/data-model.md`

**File**: `docs/data-model.md`

Two changes:

1. **Line 148** (ERD diagram): rename column in the Ticket entity block

   - Old: `VARCHAR_20 severity_override "nullable"`
   - New: `VARCHAR_20 severity_manual "nullable"`

2. **Line 1086** (Ticket table, column definition row): rename column name
   and update description to remove "override" language

   - Old: `| severity_override | VARCHAR(20) | nullable | Manual severity set by the VA (Critical, High, Medium, Low, None). \`NULL\` = not set (unresolved). \`None\` = VA explicitly assessed as informational (equivalent to CVSS score 0.0). Used for severity resolution when \`cve_id IS NULL\`. Ignored when \`cve_id IS NOT NULL\` (automatic severity from CVSS takes precedence). See \`docs/features/tickets/tickets.md\` (Severity Resolution) |`
   - New: `| severity_manual | VARCHAR(20) | nullable | Manual severity set by the VA (Critical, High, Medium, Low, None). \`NULL\` = not set (unresolved). \`None\` = VA explicitly assessed as informational (equivalent to CVSS score 0.0). Used for severity resolution when \`cve_id IS NULL\`. Cannot be set when \`cve_id IS NOT NULL\` (severity is derived from CVSS). See \`docs/features/tickets/tickets.md\` (Severity Resolution) |`

   Note: also replace "Ignored when `cve_id IS NOT NULL` (automatic
   severity from CVSS takes precedence)" with "Cannot be set when
   `cve_id IS NOT NULL` (severity is derived from CVSS)" — this aligns
   the description with the actual behavior (`SeverityDerivedError`
   prevents setting, rather than the field being ignored).

---

### Step 2 — `docs/api-spec.md`

**File**: `docs/api-spec.md`

One change:

1. **Line 684** (Mutation Patterns example): fix the JSON body key to
   match the actual endpoint spec (which uses `"severity"` as the
   request body key — see `tickets.md`, Set Severity Manual endpoint)

   - Old: `Body: {"severity_override": "critical"}`
   - New: `Body: {"severity": "critical"}`

   Note: the old value `"severity_override"` was already inconsistent
   with the endpoint spec in `tickets.md` (which uses `"severity"`).
   This step fixes both the rename and the pre-existing inconsistency.

---

### Step 3 — `docs/features/tickets/tickets.md`

**File**: `docs/features/tickets/tickets.md`

Changes (in document order):

1. **Line 197** (Resolution rule #2):
   - Old: `severity = \`ticket.severity_override\``
   - New: `severity = \`ticket.severity_manual\``

2. **Line 200** (Section header):
   - Old: `### severity_override Field`
   - New: `### severity_manual Field`

3. **Line 202** (Field definition):
   - Old: `- \`Ticket.severity_override\`: VARCHAR(20) ...`
   - New: `- \`Ticket.severity_manual\`: VARCHAR(20) ...`

4. **Lines 206-208** (CVE association behavior): reword to remove
   "override" and "ignored" language, align with the actual constraint

   - Old: `- When a CVE is associated later, the automatic severity from CVSS takes over and \`severity_override\` is ignored (but not deleted — it serves as a historical record of the VA's initial assessment)`
   - New: `- When a CVE is associated later, the automatic severity from CVSS takes over and \`severity_manual\` is preserved (but not used — it serves as a historical record of the VA's initial assessment)`

   Rationale: "ignored" implied the field existed alongside CVSS
   derivation; "preserved (but not used)" accurately describes that the
   field retains its value as a historical record while severity is now
   derived from CVSS.

5. **Line 279** (Gate #3 context):
   - Old: `For tickets without CVE, \`severity_override\` must be set by the VA`
   - New: `For tickets without CVE, \`severity_manual\` must be set by the VA`

6. **Line 667** (Behavioral Differences table):
   - Old: `| Severity | Manual via \`severity_override\` (editable by VA) |`
   - New: `| Severity | Manual via \`severity_manual\` (editable by VA) |`

7. **Line 672** (Behavioral Differences table):
   - Old: `| Gate: SUSE CVSS required | Not applicable — severity is set via \`severity_override\` instead |`
   - New: `| Gate: SUSE CVSS required | Not applicable — severity is set via \`severity_manual\` instead |`

8. **Line 1051** (TicketSummary response schema, severity field description):
   - Old: `Resolved severity (CVSS-derived → override fallback). Values: ... \`null\` = no CVSS data and no override. ...`
   - New: `Resolved severity (CVSS-derived → manual fallback). Values: ... \`null\` = no CVSS data and no manual severity set. ...`

9. **Line 1070** (TicketDetail response schema, severity field description):
   same change as item 8 (identical text in both schemas).

10. **Line 1199** (Create Ticket API, severity parameter description):
    - Old: `- \`severity\` (string, optional): initial severity override (critical, ...`
    - New: `- \`severity\` (string, optional): initial manual severity (critical, ...`

    Note: the API body field name remains `severity` (not
    `severity_manual`) because this is a mutation endpoint that sets the
    DB column `severity_manual` via the `severity` request key. This is
    consistent with the existing pattern (the Set Severity endpoint also
    uses `"severity"` as the request key).

11. **Line 1259** (Endpoint section header):
    - Old: `### Set Severity Override`
    - New: `### Set Severity Manual`

12. **Line 1268** (Endpoint description):
    - Old: `Sets or clears the severity override for a ticket without a CVE.`
    - New: `Sets or clears the manual severity for a ticket without a CVE.`

13. **Line 1278** (Clear example prose):
    - Old: `To clear the override (revert to unresolved):`
    - New: `To clear the manual severity (revert to unresolved):`

14. **Lines 1286-1288** (Parameter description):
    - Old: `medium, low, none) sets the severity override; JSON \`null\` clears the override (sets \`severity_override\` to SQL NULL = unresolved)`
    - New: `medium, low, none) sets the manual severity; JSON \`null\` clears it (sets \`severity_manual\` to SQL NULL = unresolved)`

15. **Line 1600** (Schema table, column row):
    - Old: `| severity_override | VARCHAR(20) | nullable | Manual severity (Critical, High, Medium, Low, None). NULL = not set (unresolved). \`None\` = VA explicitly set informational severity (CVSS score 0.0). Used when \`cve_id IS NULL\` |`
    - New: `| severity_manual | VARCHAR(20) | nullable | Manual severity (Critical, High, Medium, Low, None). NULL = not set (unresolved). \`None\` = VA explicitly set informational severity (CVSS score 0.0). Used when \`cve_id IS NULL\` |`

16. **Line 1616** (Security section):
    - Old: `Assigning, changing status, associating CVE, setting severity override: \`triage_ticket\` capability`
    - New: `Assigning, changing status, associating CVE, setting manual severity: \`triage_ticket\` capability`

---

### Step 4 — `docs/features/tickets/ticket-mutations.md`

**File**: `docs/features/tickets/ticket-mutations.md`

Changes (in document order):

1. **Line 6** (Purpose, prose):
   - Old: `CVSS assessment management, severity overrides, and`
   - New: `CVSS assessment management, manual severity, and`

2. **Line 40** (Invocation pattern table):
   - Old: `await ticket_mutations.set_severity_override(session, ...)`
   - New: `await ticket_mutations.set_severity_manual(session, ...)`

3. **Line 210** (reconcile_ticket_status, tickets without CVE):
   - Old: `severity from \`severity_override\`, not from CVSS assessments`
   - New: `severity from \`severity_manual\`, not from CVSS assessments`

4. **Line 396** (Consumers table):
   - Old: `| \`ticket_mutations\` | \`upsert_cvss_assessment\`\*, \`delete_cvss_assessment\`\*, \`set_severity_override\` |`
   - New: `| \`ticket_mutations\` | \`upsert_cvss_assessment\`\*, \`delete_cvss_assessment\`\*, \`set_severity_manual\` |`

5. **Line 586** (Function header):
   - Old: `### \`set_severity_override()\``
   - New: `### \`set_severity_manual()\``

6. **Line 588** (Function description):
   - Old: `Sets or clears the \`severity_override\` field on a ticket.`
   - New: `Sets or clears the \`severity_manual\` field on a ticket.`

7. **Line 596** (Parameters table, severity description): replace
   "clear the override (sets `severity_override` to SQL `NULL`..."

   - Old: `...or Python \`None\` to clear the override (sets \`severity_override\` to SQL \`NULL\` = unresolved)`
   - New: `...or Python \`None\` to clear the value (sets \`severity_manual\` to SQL \`NULL\` = unresolved)`

8. **Line 613** (Behavior step 6):
   - Old: `6. Update \`ticket.severity_override\``
   - New: `6. Update \`ticket.severity_manual\``

9. **Line 618** (Gate relevance):
   - Old: `**Gate relevance**: setting \`severity_override\` affects the ticket's`
   - New: `**Gate relevance**: setting \`severity_manual\` affects the ticket's`

10. **Line 622** (Gate relevance continued):
    - Old: `resolution cascade and \`severity_override\` is not applicable.`
    - New: `resolution cascade and \`severity_manual\` is not applicable.`

11. **Line 604** (Preconditions, prose): remove "overridden" language
    - Old: `scores and cannot be manually overridden)`
    - New: `scores and cannot be set manually)`

12. **Line 888** (Module ownership):
    - Old: `(\`CVECVSSAssessment\` records, severity override)`
    - New: `(\`CVECVSSAssessment\` records, manual severity)`

---

### Step 5 — `docs/features/tickets/ticket-service.md`

**File**: `docs/features/tickets/ticket-service.md`

Changes (in document order):

1. **Line 19** (Purpose, prose — if present):
   - Old: `Gate-relevant mutations (CVSS assessments, severity overrides,...`
   - New: `Gate-relevant mutations (CVSS assessments, manual severity,...`

2. **Line 97** (Scope Boundary dispatch table):
   - Old: `| \`PATCH .../severity\` | \`set_severity_override()\` | Gate-relevant (severity affects Analyzed gate #3) |`
   - New: `| \`PATCH .../severity\` | \`set_severity_manual()\` | Gate-relevant (severity affects Analyzed gate #3) |`

3. **Line 152** (create_ticket signature):
   - Old: `severity_override: Severity | None = None,`
   - New: `severity_manual: Severity | None = None,`

4. **Line 172** (Preconditions):
   - Old: `- If both \`cve_id\` and \`severity_override\` are provided: the service`
   - New: `- If both \`cve_id\` and \`severity_manual\` are provided: the service`

5. **Line 174** (Preconditions continued): reword "manual override is not
   applicable"
   - Old: `derived exclusively from CVSS assessments — manual override is not applicable.`
   - New: `derived exclusively from CVSS assessments — manual severity is not applicable.`

6. **Line 189** (Behavioral step 5):
   - Old: `5. If \`severity_override\` provided: create \`TicketAuditEvent\``
   - New: `5. If \`severity_manual\` provided: create \`TicketAuditEvent\``

7. **Line 190** (Behavioral step 5, audit event value placeholder):
   - Old: `(\`severity_changed\`, \`old_value = NULL\`, \`new_value = <override>\`)`
   - New: `(\`severity_changed\`, \`old_value = NULL\`, \`new_value = <severity>\`)`

8. **Line 243** (associate_cve behavior, recalculate_cvss_chain):
   - Old: `\`severity_override\` to CVSS-cascade-derived) and product eligibility`
   - New: `\`severity_manual\` to CVSS-cascade-derived) and product eligibility`

9. **Line 753** (Architectural Test Requirement #1):
   - Old: `CVE, set \`severity_override\`, add a package with tracks in final`
   - New: `CVE, set \`severity_manual\`, add a package with tracks in final`

10. **Line 771** (Architectural Test Requirement #7):
    - Old: `function on an unassigned ticket, e.g., \`set_severity_override\`,`
    - New: `function on an unassigned ticket, e.g., \`set_severity_manual\`,`

---

### Step 6 — `docs/features/tickets/cvss-scoring.md`

**File**: `docs/features/tickets/cvss-scoring.md`

Changes:

1. **Line 265** (Severity Derivation note):
   - Old: `\`severity_override\` field on the Ticket, set manually by the VA.`
   - New: `\`severity_manual\` field on the Ticket, set manually by the VA.`

2. **Line 311** (Workflow Gates):
   - Old: `set \`severity_override\` before the ticket can progress.`
   - New: `set \`severity_manual\` before the ticket can progress.`

---

### Step 7 — `docs/features/tickets/ticket-audit-log.md`

**File**: `docs/features/tickets/ticket-audit-log.md`

Two changes on the same line:

1. **Line 41** (`severity_changed` event type description): replace both
   "override" prose references and the function name

   - Old (description): `...or VA sets/clears severity override (manual)`
   - New (description): `...or VA sets/clears manual severity`

   - Old (user_id column): `...acting user's UUID for manual severity override (\`set_severity_override()\`)`
   - New (user_id column): `...acting user's UUID for manual severity (\`set_severity_manual()\`)`

---

### Step 8 — `docs/features/identity/rbac.md`

**File**: `docs/features/identity/rbac.md`

Changes:

1. **Line 25** (Capability description):
   - Old: `set/update severity override`
   - New: `set/update manual severity`

2. **Line 144** (Operations table):
   - Old: `| Set/update severity override | \`triage_ticket\` |`
   - New: `| Set/update manual severity | \`triage_ticket\` |`

3. **Line 379** (Endpoint Permission Map): update the anchor reference

   - Old: `| PATCH | \`/api/v1/tickets/{ticket_id}/severity\` | \`triage_ticket\` | [tickets](../tickets/tickets.md#set-severity-override) |`
   - New: `| PATCH | \`/api/v1/tickets/{ticket_id}/severity\` | \`triage_ticket\` | [tickets](../tickets/tickets.md#set-severity-manual) |`

   Note: the endpoint URL path (`/severity`) does NOT change — it
   describes what is being set (severity), not the storage mechanism.
   Only the section header anchor changes because the header was renamed
   in Step 3.

---

### Step 9 — `.opencode/agents/ticket-integrity-reviewer.md`

**File**: `.opencode/agents/ticket-integrity-reviewer.md`

One change:

1. **Line 117**:
   - Old: `(\`severity_override\` or CVSS-derived)`
   - New: `(\`severity_manual\` or CVSS-derived)`

---

### Step 10 — Verification (reviewers)

After applying all changes from Steps 1-9, invoke the following
reviewers to verify correctness:

1. **`@spec-coherence-reviewer`** on:
   - `docs/features/tickets/tickets.md` — primary spec affected,
     cross-references with all other ticket specs
   - `docs/features/tickets/ticket-mutations.md` — function rename,
     cross-references with ticket-service and rbac
   - `docs/features/tickets/ticket-service.md` — parameter rename,
     cross-references with ticket-mutations
   - `docs/features/tickets/cvss-scoring.md` — field reference change

   Run one review per spec as prescribed by Guardrail 15.

2. **`@data-model-reviewer`** — verify `docs/data-model.md` column
   rename is consistent with conventions and no description
   contradictions were introduced

3. **`@api-convention-reviewer`** on:
   - `docs/features/tickets/tickets.md` — endpoint header rename and
     body key change

4. **`@docs-reviewer`** — verify documentation completeness across
   modified files (no stale references remain)

---

### Step 11 — Delete this draft

After all reviewer findings are addressed (or confirmed as clean), delete
this file:

```
docs/drafts/rename-severity-override-to-severity-manual.md
```

## Design Notes

### API body field name: `"severity"` (unchanged)

The `PATCH /api/v1/tickets/{ticket_id}/severity` endpoint uses
`"severity"` as the JSON body key, not `"severity_manual"`. This is
correct because:

- The endpoint's URL already identifies *what* is being set (severity)
- The body contains the *value*, not a reference to the column name
- The `POST /api/v1/tickets` endpoint similarly uses `"severity"` for the
  initial manual severity value
- Only the `api-spec.md` Mutation Patterns *example* used
  `"severity_override"` as the body key — this was a documentation error
  independent of this rename (the actual endpoint spec in `tickets.md`
  already uses `"severity"`)

### Column description: "Cannot be set" vs "Ignored"

The old `data-model.md` description said: "Ignored when `cve_id IS NOT
NULL` (automatic severity from CVSS takes precedence)." This is
inaccurate — `set_severity_manual()` raises `SeverityDerivedError` when
`cve_id IS NOT NULL`, which means the field cannot be set at all (not
that it is set but ignored). The new description says: "Cannot be set
when `cve_id IS NOT NULL` (severity is derived from CVSS)."

### Endpoint URL path: unchanged

The URL path `/api/v1/tickets/{ticket_id}/severity` is NOT renamed. It
describes the *concept* being mutated (severity), not the storage field.
This is consistent with the PATCH semantics (the URL identifies the
sub-resource, not the implementation detail).

### Prose transitions

Where the old specs used "severity override" as a noun phrase in prose
(not a code reference), the new text uses "manual severity" — which
reads naturally in English ("set the manual severity", "manual severity
is not applicable").
