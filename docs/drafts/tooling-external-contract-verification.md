# Draft: Implementation Tooling — External Contract Verification and Implement-Slice Workflow

## Summary

Enhance the OpenCode tooling to support disciplined, phased implementation:

1. **Refine the Code agent prompt** (`.opencode/prompts/code.md`): add
   Definition of Done for each implementation slice, external service
   contract verification protocol, and awareness of implementation layers
2. **Create `@external-contract-verifier` subagent**: a read-only reviewer
   that validates request/response shapes against live or documented
   external service contracts
3. **Create `implement-slice` skill**: a guided, repeatable workflow for
   implementing one vertical slice of the system (from spec reading through
   testing and review)

## Rationale

1. **External service correctness is a first-class concern**: multiple
   fetchers integrate with NVD, MITRE, Red Hat, SMELT, AIMAAS, IBS, and
   others. Field names, response structures, pagination patterns, and
   authentication methods must be verified against real service behavior —
   not assumed from documentation alone
2. **Repeatable implementation workflow**: each implementation slice follows
   the same sequence (read spec → plan → models → migration → service →
   API → tests → reviewers → smoke test). Encoding this as a skill ensures
   consistency and prevents steps from being skipped
3. **Definition of Done prevents premature advancement**: without explicit
   completion criteria, there is a risk of moving to the next slice with
   untested or unreviewed code

## Scope of Changes

### Files to CREATE

| Path | Purpose |
|------|---------|
| `.opencode/agents/external-contract-verifier.md` | New subagent definition |
| `.opencode/skills/implement-slice/SKILL.md` | New skill definition |

### Files to MODIFY

| Path | Nature of change |
|------|-----------------|
| `.opencode/prompts/code.md` | Add §Definition of Done, §External Contract Verification, §Implementation Layers |
| `.opencode/README.md` | Add new subagent and skill to catalog |

---

## Action Plan

### Step 1 — Refine .opencode/prompts/code.md

Add three new sections to the Code agent prompt. Insert them AFTER the
existing "Implementation Standards" section and BEFORE "Reviewer
Invocation". The full content for each section follows.

#### 1.1 — Add "Definition of Done" section

Insert after "### After Implementation" (currently the last subsection of
"Implementation Standards"):

```markdown
## Definition of Done

A slice is complete ONLY when ALL of the following are satisfied:

1. **Tests pass**: `cd backend && pytest` exits 0 with no failures
2. **Lint clean**: `cd backend && ruff check . && ruff format --check .`
   exits 0
3. **Coverage adequate**: new code has test coverage for happy path,
   error paths, and permission enforcement
4. **Reviewers executed**: invoke the relevant reviewers per the
   "Reviewer Invocation" section below. If a reviewer flags "Needs
   revision", address the issue before declaring done
5. **External contracts verified** (if the slice integrates with an
   external service): the contract verification protocol below has been
   followed
6. **No spec deviations**: implementation matches the specification
   exactly. If deviations were needed, the Gap Protocol was followed and
   the spec was updated with user approval

Do NOT inform the user that a slice is "done" until all six criteria are
met. If any criterion cannot be satisfied (e.g., a test environment is
unavailable), explicitly state which criterion is unmet and why.
```

#### 1.2 — Add "External Contract Verification" section

Insert immediately after "Definition of Done":

```markdown
## External Contract Verification

When implementing code that parses responses from or sends requests to an
external service (NVD, MITRE, Red Hat, SMELT, AIMAAS, IBS, GitHub, CISA,
FIRST/EPSS, OSV, git.kernel.org), follow this protocol:

### Before writing the parser/client

1. **Identify the documented contract**: read `docs/data-sources.md` and
   the relevant fetcher specification for the expected request/response
   format
2. **Obtain a real response sample**: for public APIs (NVD, Red Hat, CISA,
   FIRST, OSV, GitHub), make a direct HTTP request to capture a real
   response. For internal SUSE APIs (IBS, SMELT, AIMAAS), use `secbox`
   for exploratory access ONLY (never in application code). For git-based
   sources, perform a manual clone/fetch to observe the file format
3. **Compare field names and structure**: verify that the real response
   matches the documented contract in `data-sources.md`/fetcher spec. Pay
   attention to: field names (camelCase vs snake_case), nesting levels,
   pagination format, date formats, nullable fields, array vs object
4. **If discrepancy found**: STOP. Do not guess. Signal the discrepancy
   to the user with:
   - The expected format (from spec)
   - The actual format (from real response)
   - A proposal for resolving the discrepancy (update spec, or adjust
     implementation)
5. **Sanitize and save as fixture**: replace all PII (Guardrail 23) with
   fictional data. Save the sanitized response as a test fixture in
   `backend/tests/fixtures/<service_name>/` for use in contract tests

### During implementation

6. **Write contract tests first**: before writing the parser, write tests
   that load the fixture and verify the parser produces the expected
   output. This ensures the parser is tested against a known-good shape
7. **Use typed response models**: where practical, define Pydantic models
   or TypedDicts for external response structures. This makes field name
   mismatches fail loudly at parse time

### When in doubt

8. If the specification is ambiguous about the external service's behavior
   (e.g., optional fields, error response format, pagination edge cases),
   invoke `@external-contract-verifier` to verify against the live service,
   OR ask the user for guidance. Do NOT make assumptions about external
   service behavior.
```

#### 1.3 — Add "Implementation Layers" section

Insert immediately after "External Contract Verification":

```markdown
## Implementation Layers

Sentinel is built in dependency-ordered layers. When implementing a slice,
be aware of what layer it belongs to and what prerequisites must exist:

| Layer | Content | Prerequisites |
|-------|---------|---------------|
| 0 | Infrastructure (ORM base, Celery factory, test fixtures, health) | None |
| 1 | Identity (User, RBAC, auth, CLI bootstrap) | Layer 0 |
| 2 | Platform (settings, BaseFetcher, fetcher-operations, networking) | Layer 1 |
| 3 | CVE + Ticket core (models, services, mutations, references) | Layers 1–2 |
| 4 | CVE fetchers (BaseCVEFetcher, BaseGitFetcher, concrete fetchers) | Layer 3 |
| 5 | Package resolution (package-service, package-model endpoints) | Layer 3 |

Do NOT implement artifacts from a higher layer before its prerequisites
are complete and tested. If you discover a missing prerequisite during
implementation, STOP and signal it rather than implementing it ad-hoc.
```

### Step 2 — Create .opencode/agents/external-contract-verifier.md

Create the file with the following content:

```markdown
---
description: >
  Reviews external service integration code to verify that request/response
  structures match the documented contracts in data-sources.md and feature
  specs. Can optionally verify against live services. Use this agent when
  implementing or modifying fetchers, HTTP clients, or parsers that interact
  with external APIs. Read-only: does not modify files.
mode: subagent
permission:
  edit: deny
  bash:
    "curl *": allow
    "secbox osc *": allow
    "git clone *": allow
    "git ls-remote *": allow
    "*": deny
---

## Role

You verify that Sentinel's integration code correctly handles the
request/response contracts of external services. You compare implementation
code against: (1) the documented contract in `docs/data-sources.md` and
feature specs, and (2) optionally, a live response from the actual service.
You do NOT write or modify code.

## Before reviewing

1. Read `docs/data-sources.md` to understand the catalog of external services,
   their endpoints, authentication methods, and response formats
2. Read the relevant fetcher specification (e.g.,
   `docs/features/tickets/cve-sync-nvd.md` for NVD integration)
3. Read the implementation code being reviewed (parser, client, or fetcher)

## Verification methods

### Method A — Documentation-only (default)

Compare the implementation's field access patterns (dictionary keys, JSON
paths, attribute names) against the documented response structure in
`data-sources.md` and the fetcher spec. Flag any field name that:
- Does not appear in the documented structure
- Uses different casing than documented
- Accesses a path at the wrong nesting level
- Assumes a field is always present when the spec marks it nullable/optional

### Method B — Live verification (when requested or when documentation is ambiguous)

Make a real request to the external service to capture the actual response
structure. Use:
- Direct `curl` for public APIs (NVD, Red Hat, CISA, FIRST, OSV, GitHub)
- `secbox osc api ...` for IBS/OBS (NEVER bare `osc`)
- `git ls-remote` or `git clone --bare --depth=1` for git-based sources

Compare the live response against both the documentation AND the
implementation. Report any three-way discrepancy (doc vs live vs code).

**Important**: when fetching live data, sanitize any PII before including
response samples in the review output (Guardrail 23). Use fictional
replacements for person names, email addresses, and userids.

## What to check

1. **Field name accuracy**: every dictionary key or JSON path accessed in
   the parser matches the real field name (case-sensitive)
2. **Nesting correctness**: values are extracted from the correct level of
   the response hierarchy
3. **Pagination handling**: the implementation follows the service's actual
   pagination pattern (offset, cursor, next-page link, etc.)
4. **Error response handling**: the implementation handles the service's
   actual error format (not a guessed format)
5. **Authentication**: credentials are passed in the correct header/parameter
   format for the service
6. **Rate limiting**: the implementation respects documented rate limits and
   handles 429 responses correctly
7. **Date/time formats**: parsed correctly (ISO 8601, Unix epoch, or
   service-specific format)

## Output

Provide a structured report:

1. **Verified fields**: fields that match the documented/live contract
2. **Discrepancies**: fields or patterns that do NOT match, with:
   - Expected (from doc/live)
   - Actual (from code)
   - Severity: Critical (will cause runtime failure), Medium (may cause
     silent data loss), Low (cosmetic or unlikely edge case)
3. **Undocumented assumptions**: patterns in the code that assume behavior
   not documented anywhere (flag for spec update)
4. **Recommendation**: proceed / fix before proceeding / update spec first

## Scope limitations

- You verify **structure and naming** (static correctness), not runtime
  behavior or performance
- You do NOT verify business logic (e.g., whether the correct CVE fields
  are stored — that is the domain of `@fetcher-compliance-reviewer`)
- You do NOT make requests that would modify external state (no POST/PUT
  to external services)
- If a service requires credentials you do not have access to, report
  "unable to verify live — documentation-only review performed" and
  proceed with Method A
```

### Step 3 — Create .opencode/skills/implement-slice/SKILL.md

Create the file with the following content:

```markdown
---
name: implement-slice
description: Guided workflow for implementing one vertical slice of the Sentinel backend. Ensures correct ordering (spec → models → migration → service → API → tests → reviewers) and enforces the Definition of Done.
---

## Workflow: Implementing a Vertical Slice

Follow these steps in order when implementing a single feature slice.
A "slice" is a coherent unit of functionality defined by one or more
feature specifications (e.g., "health endpoints", "BaseFetcher +
FetcherRun model", "local authentication login endpoint").

### Step 0: Identify the slice and its spec(s)

1. Confirm with the user which slice is being implemented
2. Read the relevant feature specification(s) COMPLETELY
3. Identify the implementation layer (0–5) per the Code agent's
   "Implementation Layers" table
4. Verify that all prerequisite layers are already implemented and tested.
   If not, STOP and inform the user: "This slice depends on [prerequisite]
   which is not yet implemented."

### Step 1: Plan the artifacts

Before writing any code, produce a brief plan listing:

- **Models** to create/modify (with table names and key columns)
- **Alembic migration** needed (yes/no)
- **Service modules** to create/modify
- **API endpoints** to create/modify (method, path, access level)
- **CLI commands** to create/modify (if any)
- **Celery tasks** to create/modify (if any)
- **Test files** to create (mirroring app/ structure)
- **Config/env vars** introduced (if any)
- **External service dependencies** (if any — triggers contract verification)

Present this plan to the user for confirmation before proceeding.

### Step 2: Database layer (models + migration)

1. Create or modify SQLAlchemy models in `backend/app/models/`
2. Follow conventions: UUID PKs, `created_at`/`updated_at` timestamps,
   explicit `back_populates`, proper type hints
3. Register models in `backend/app/models/__init__.py`
4. Generate migration: `alembic revision --autogenerate -m "<description>"`
5. Review the generated migration for correctness (autogenerate can miss
   things or generate unwanted changes)
6. Apply migration: `alembic upgrade head`
7. Verify: `alembic current` shows the new head

### Step 3: Service layer

1. Create or modify service modules in `backend/app/services/`
2. Follow the spec exactly — if the spec defines function signatures,
   guards, exceptions, and audit events, implement them precisely
3. If the service integrates with an external service, follow the
   "External Contract Verification" protocol from the Code agent prompt
4. Ensure audit events are created atomically in the same transaction
   (Guardrail 11)
5. Use centralized mutation modules where required (Guardrail 16)

### Step 4: API layer (if the slice includes endpoints)

1. Create or modify endpoint handlers in `backend/app/api/v1/`
2. Keep handlers thin: validate → call service → return response
3. Apply `require_capability()` per the spec's access level
4. Create Pydantic request/response schemas in `backend/app/schemas/`
5. Wire the router into the FastAPI app

### Step 5: CLI layer (if the slice includes CLI commands)

1. Create or modify Click commands
2. Follow the CLI Output Contract (docs/conventions.md)
3. Use synchronous DB sessions (not async)
4. Ensure idempotency where specified

### Step 6: Write tests

1. **Unit tests** for pure functions (parsers, validators, resolvers)
2. **Integration tests** for services (with real Postgres via test fixtures)
3. **API tests** for endpoints (via httpx AsyncClient):
   - Happy path
   - Validation errors (bad input)
   - Permission enforcement (missing capability → 403)
   - Not found (invalid ID → 404)
4. **Contract tests** for external service parsers (using saved fixtures)
5. **CLI tests** if CLI commands were added

Run the full test suite: `cd backend && pytest -v`

### Step 7: Verify Definition of Done

Check ALL six criteria from the Code agent's "Definition of Done":

1. ☐ Tests pass (`pytest` exits 0)
2. ☐ Lint clean (`ruff check . && ruff format --check .` exits 0)
3. ☐ Coverage adequate (new code has tests for happy + error + permissions)
4. ☐ Reviewers executed (see Step 8)
5. ☐ External contracts verified (if applicable)
6. ☐ No spec deviations (or Gap Protocol followed)

### Step 8: Invoke reviewers

Based on what was implemented, invoke the relevant reviewers:

| Artifact type | Reviewer |
|---------------|----------|
| New/modified models or migrations | `@data-model-reviewer` |
| New/modified API endpoints | `@security-reviewer` |
| New/modified fetchers | `@fetcher-compliance-reviewer` |
| Ticket mutations | `@ticket-integrity-reviewer` |
| Identity mutations | `@identity-integrity-reviewer` |
| New tests | `@test-reviewer` |
| External service integration | `@external-contract-verifier` |

If any reviewer flags "Needs revision", address the issue before
proceeding to Step 9.

### Step 9: Report completion

Inform the user that the slice is complete, summarizing:
- What was implemented (models, services, endpoints, commands)
- Test count and coverage highlights
- Any reviewer findings that were addressed
- Any spec clarifications that were made (Gap Protocol)
- What the next logical slice would be (based on the layer order)
```

### Step 4 — Update .opencode/README.md

4.1. In the **Subagents** table, add a new row (maintain alphabetical order
by agent name):

```markdown
| `@external-contract-verifier` | Reviewer | On-demand | Verifies external service request/response structures match documented contracts |
```

4.2. In the **Skills** table, add a new row:

```markdown
| `implement-slice` | Guided workflow for implementing one vertical backend slice with spec compliance and Definition of Done enforcement |
```

### Step 5 — Verify coherence

5.1. Read the final `.opencode/prompts/code.md` and verify that:
- The new sections do not contradict existing sections
- The "Implementation Layers" table is consistent with the dependency
  analysis in the implementation plan discussion
- The "External Contract Verification" protocol is consistent with
  Guardrails 14 (fetcher compliance) and 23 (no real PII)
- The "Definition of Done" criteria reference sections that exist

5.2. Read `@external-contract-verifier` agent and verify that:
- The bash permissions allow only read-only external access (curl, secbox
  osc, git clone/ls-remote) — no write operations
- The scope limitations clearly delineate this agent from
  `@fetcher-compliance-reviewer` (structure vs. behavior)
- The PII sanitization reminder is present (Guardrail 23)

5.3. Read `implement-slice` skill and verify that:
- The step order matches the dependency direction (models before services
  before API before tests)
- The reviewer table is consistent with the Guardrails in AGENTS.md
- The "prerequisite check" in Step 0 prevents out-of-order implementation
- The Definition of Done criteria match the Code agent prompt exactly

### Step 6 — Run reviewers

Invoke the following reviewers on the modified/created files:

- `@docs-reviewer` on `.opencode/prompts/code.md` (verify documentation
  completeness of the new protocol sections)
- Manually verify that the `implement-slice` skill works by doing a
  dry-run mental walkthrough against a concrete slice (e.g., "health
  endpoints" — the simplest possible slice)

### Step 7 — Delete this draft

Delete `docs/drafts/tooling-external-contract-verification.md`.

---

## Decision Record

- **Code agent prompt**: enhanced with Definition of Done, external contract
  verification protocol, and layer awareness. These are operational rules
  the agent enforces during implementation
- **`@external-contract-verifier`**: new read-only subagent with limited
  bash access (curl, secbox osc, git clone). Does NOT modify files. Used
  on-demand during external service integration work
- **`implement-slice` skill**: loaded automatically when implementing a
  vertical slice. Provides the repeatable checklist that ensures nothing
  is skipped
- **No changes to `opencode.json`**: the new skill is auto-discovered from
  `.opencode/skills/` and the new agent from `.opencode/agents/`; no manual
  registration needed
- **Relationship between §6 and §7**: the external contract verification
  practice (§6) is realized both as a protocol in the Code agent prompt
  (behavioral rule) and as the `@external-contract-verifier` subagent
  (verification tool). The two work together: the Code agent follows the
  protocol during implementation; the subagent is invoked for verification
  when ambiguity exists
