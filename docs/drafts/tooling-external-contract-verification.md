# Draft: Implementation Tooling — External Contract Verification

## Summary

Enhance the OpenCode tooling to support disciplined external service
integration:

1. **Refine the Code agent prompt** (`.opencode/prompts/code.md`): add
   Definition of Done gate, External Contract Verification protocol,
   dependency-aware "Before Starting" section, and reviewer entry for
   external service integration
2. **Create `@external-contract-verifier` subagent**: a read-only reviewer
   that validates request/response shapes against real upstream service
   contracts

## Rationale

1. **External service correctness is a first-class concern**: multiple
   fetchers integrate with NVD, MITRE, Red Hat, SMELT, AIMAAS, IBS, and
   others. Field names, response structures, pagination patterns, and
   authentication methods must be verified against real service behavior —
   not assumed from documentation alone
2. **Explicit completion gate**: without a consolidated Definition of Done,
   the Code agent may declare a task complete with unrun reviewers, failing
   lint, or unverified external contracts. The DoD is a checklist gate that
   prevents premature advancement

## Scope of Changes

### Files to CREATE

| Path | Purpose |
|------|---------|
| `.opencode/agents/external-contract-verifier.md` | New subagent definition |

### Files to MODIFY

| Path | Nature of change |
|------|-----------------|
| `.opencode/prompts/code.md` | Add §Definition of Done, §External Contract Verification, expand §Before Starting, add reviewer entry |
| `.opencode/README.md` | Add new subagent to catalog |

---

## Action Plan

### Step 1 — Refine .opencode/prompts/code.md

Add new sections and modify existing ones. The full content for each
change follows.

#### 1.1 — Replace "Before Starting" subsection

Replace the current "### Before Starting" content (lines 86–91) with:

```markdown
### Before Starting

1. Read the relevant specification completely
2. Identify all files that need to be created or modified
3. Verify prerequisites: confirm that the direct dependencies of the
   artifacts you will implement (the models and services they build on)
   already exist and are tested. Identify these from the feature spec and
   `docs/data-model.md`. If a prerequisite is missing, STOP and signal it
   rather than implementing it ad-hoc
4. Plan the implementation order (models → services → API → tests, or as
   appropriate) and briefly confirm the intended artifacts with the user
   before implementing
```

#### 1.2 — Add "Definition of Done" section

Insert after "### After Implementation" (currently the last subsection of
"Implementation Standards"), before "## Reviewer Invocation":

```markdown
## Definition of Done

A slice is complete ONLY when ALL of the following are satisfied:

1. **Guardrails met**: all applicable AGENTS.md Guardrails are satisfied
   (tests pass and cover happy/error/permission paths per G6, lint clean,
   reviewers invoked per G8–G17, no spec deviations per G1, Gap Protocol
   followed if deviations were needed)
2. **External contracts verified** (if the slice integrates with an
   external service): the External Contract Verification protocol below
   has been followed

Do NOT inform the user that a slice is "done" until both criteria are
met. If any criterion cannot be satisfied (e.g., a test environment is
unavailable), explicitly state which criterion is unmet and why.
```

#### 1.3 — Add "External Contract Verification" section

Insert immediately after "Definition of Done":

```markdown
## External Contract Verification

When implementing code that parses responses from or sends requests to an
external service (NVD, MITRE, Red Hat, SMELT, AIMAAS, IBS, GitHub, CISA,
FIRST/EPSS, OSV, git.kernel.org), the request/response structures actually
used in the code MUST be verified against the real upstream service during
implementation — not assumed from documentation alone.

### Identify the documented contract (starting point)

1. **Read the owning fetcher specification** — this is the primary source
   for documented response field mappings (e.g., `cve-sync-nvd.md` for NVD
   field paths). `docs/data-sources.md` is secondary: it provides service
   metadata only (URLs, authentication, rate limits) — NOT response
   structures
2. **Expect gaps**: the fetcher spec may be incomplete, ambiguous, or
   outdated. Treat the spec as a starting point, not the final word on the
   actual response format

### Verify against the real upstream service (mandatory)

3. **Obtain a real response sample**: for public APIs (NVD, Red Hat, CISA,
   FIRST, OSV, GitHub), make a direct HTTP request. For SUSE internal HTTPS
   services (SMELT at `smelt.suse.de/api`, AIMAAS at `aimaas.suse.de/api`),
   use `curl` directly from the SUSE network. For IBS/OBS, use
   `secbox osc api ...` (NEVER bare `osc`; exploratory only — never in
   application code). For git-based sources, perform a manual clone/fetch to
   observe the file format
4. **Compare every field the code reads** against the real response. Pay
   attention to: field names (camelCase vs snake_case), nesting levels,
   pagination format, date formats, nullable fields, array vs object
5. **If discrepancy found**: STOP. Do not guess. Signal the discrepancy to
   the user with: the expected format (from spec), the actual format (from
   real response), and a proposal for resolution (update spec, or adjust
   implementation)
6. **Sanitize and save as fixture**: replace all PII (Guardrail 23) with
   fictional data. Save the sanitized response as a test fixture in
   `backend/tests/fixtures/<service_name>/` for use in contract tests

### During implementation

7. **Write contract tests first**: before writing the parser, write tests
   that load the fixture and verify the parser produces the expected output.
   This ensures the parser is tested against a known-good shape
8. **Use typed response models**: where practical, define Pydantic models or
   TypedDicts for external response structures. This makes field name
   mismatches fail loudly at parse time

### When the service cannot be reached

9. If the service requires credentials not available or is unreachable from
   the current network, state explicitly that verification was
   documentation-only and flag the affected fields as unverified. Do NOT
   make assumptions about external service behavior without verification.
```

#### 1.4 — Add external contract reviewer to "Reviewer Invocation" section

In the existing "## Reviewer Invocation" section of `code.md`, add the
following bullet points after the `@docs-reviewer` entry:

```markdown
- **External service integration** → suggest `@external-contract-verifier`
- **New external integration involving credentials, response parsing, or a new parser dependency** → also suggest `@security-reviewer`
```

### Step 2 — Create .opencode/agents/external-contract-verifier.md

Create the file with the following content:

```markdown
---
description: >
  Reviews external service integration code to verify that request/response
  structures match real upstream contracts. Can optionally verify against
  live services. Use this agent when implementing or modifying fetchers,
  HTTP clients, or parsers that interact with external APIs. Read-only:
  does not modify files.
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
code against: (1) the owning fetcher specification (primary source for
response field mappings), (2) `docs/data-sources.md` (secondary — service
metadata: URLs, auth, rate limits), and (3) optionally, a live response
from the actual service. You do NOT write or modify code.

## Before reviewing

1. Read the relevant fetcher specification to understand the documented
   response field mappings and expected structure
2. Read `docs/data-sources.md` for service metadata (endpoint URLs,
   authentication methods, rate limits, pagination patterns)
3. Read the implementation code being reviewed (parser, client, or fetcher)

## Verification methods

### Method A — Documentation-only

Compare the implementation's field access patterns (dictionary keys, JSON
paths, attribute names) against the documented response structure in the
fetcher specification. Flag any field name that:
- Does not appear in the documented structure
- Uses different casing than documented
- Accesses a path at the wrong nesting level
- Assumes a field is always present when the spec marks it nullable/optional

### Method B — Live verification (preferred when accessible)

Make a real request to the external service to capture the actual response
structure. Use:
- Direct `curl` for public APIs (NVD, Red Hat, CISA, FIRST, OSV, GitHub)
- Direct `curl` for SUSE internal HTTPS services (SMELT at
  `smelt.suse.de/api`, AIMAAS at `aimaas.suse.de/api`) — these are
  reachable from the SUSE network without special tooling
- `secbox osc api ...` for IBS/OBS (NEVER bare `osc`)
- `git ls-remote` or `git clone --bare --depth=1` for git-based sources

Compare the live response against both the documentation AND the
implementation. Report any three-way discrepancy (doc vs live vs code).

**Important**: when fetching live data, sanitize any PII before including
response samples in the review output (Guardrail 23). Use fictional
replacements for person names, email addresses, and userids.

### When live verification is not possible

If the service requires credentials not available, or is unreachable from
the current environment, report: "Unable to verify live — documentation-only
review performed. Fields marked as unverified: [list]." Proceed with
Method A and flag the limitation.

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
- If a service is unreachable, follow the "When live verification is not
  possible" protocol above
```

### Step 3 — Update .opencode/README.md

In the **Subagents** table, add a new row in its alphabetical position —
between the `@docs-reviewer` and `@fetcher-compliance-reviewer` rows.
(The `@identity-integrity-reviewer` and `@ticket-integrity-reviewer` rows
are already out of alphabetical order at the end of the table; leave them
as-is — reordering is out of scope.)

```markdown
| `@external-contract-verifier` | Reviewer | On-demand | Verifies external service request/response structures match real upstream contracts |
```

### Step 4 — Verify coherence

4.1. Read the final `.opencode/prompts/code.md` and verify that:
- The new sections do not contradict existing sections
- The "External Contract Verification" protocol is consistent with
  Guardrails 14 (fetcher compliance) and 23 (no real PII)
- The "Definition of Done" criteria reference sections that exist
- The expanded "Before Starting" does not conflict with the existing
  "Core Principle: Specs Are Your Source of Truth"
- The qualified `@security-reviewer` entry in "Reviewer Invocation" is
  consistent with Guardrail 10 triggers (new external integrations, new
  dependencies that process user input) — neither broader nor narrower

4.2. Read `@external-contract-verifier` agent and verify that:
- The bash permissions allow only read-only external access (curl, secbox
  osc, git clone/ls-remote) — no write operations
- The scope limitations clearly delineate this agent from
  `@fetcher-compliance-reviewer` (structure vs. behavior)
- The PII sanitization reminder is present (Guardrail 23)
- SMELT and AIMAAS are listed as curl-accessible (not secbox-only)
- The agent trigger is "On-demand" (no Guardrail reference in its
  description or in `AGENTS.md`)

### Step 5 — Delete this draft

Delete `docs/drafts/tooling-external-contract-verification.md`.

---

## Decision Record

- **Code agent prompt**: enhanced with Definition of Done (lightweight
  completion gate referencing AGENTS.md Guardrails + external contract
  criterion), External Contract Verification (mandatory upstream
  verification protocol), dependency-aware "Before Starting" section, and
  reviewer invocation entry for external service integration
- **`@external-contract-verifier`**: new read-only subagent with limited
  bash access (curl, secbox osc, git clone). Does NOT modify files. Used
  on-demand during external service integration work
- **No `implement-slice` skill**: the implementation workflow (models →
  services → API → tests → reviewers) is already prescribed by `code.md`
  "Implementation Standards". Adding a skill would create a parallel
  instruction set that drifts from the prompt. The planning step (artifact
  plan + user confirmation) and the completion gate (DoD) are folded
  directly into `code.md` as always-on behavior
- **No implementation layers table**: the build-order sequencing of features
  (identity → platform → CVE → fetchers → packages) is a one-time roadmap
  produced ephemerally in-session when the build starts — not a permanent
  enforcement rule. Once the system is built, a static layer table becomes
  stale and misleading. Dependency verification at implementation time uses
  the feature spec and `docs/data-model.md` directly
- **Source hierarchy for response structures**: fetcher specifications are
  the primary documented source for response field mappings.
  `docs/data-sources.md` is secondary (service metadata: URLs, auth, rate
  limits). The real upstream service is the authoritative source — direct
  verification during implementation is mandatory, not a fallback
- **No changes to `opencode.json`**: the new agent is auto-discovered from
  `.opencode/agents/`; no manual registration needed
- **Relationship between protocol and agent**: the External Contract
  Verification protocol (in `code.md`) is the behavioral rule the Code
  agent follows during implementation; the `@external-contract-verifier`
  subagent is the verification tool invoked when review is needed. The Code
  agent follows the protocol; the subagent validates the result
- **On-demand trigger, no Guardrail**: `@external-contract-verifier` is
  deliberately NOT tied to a Guardrail. Rationale: (1) live verification
  against external services is non-deterministic — unsuitable for a
  hard completion gate that must be reliable; (2) the External Contract
  Verification protocol is already always-on in `code.md`, making the
  subagent a post-hoc double-check rather than the primary mechanism;
  (3) the trigger scope ("code that touches an external service") is
  ambiguous — unlike "new API endpoint" or "new model", it resists clean
  delimitation, leading to over- or under-triggering; (4) the "soft"
  trigger (entry in Reviewer Invocation) provides the right level of
  coupling without gate fragility. Promotion to a dedicated Guardrail is
  warranted only if external contract mismatches become a recurring source
  of production bugs
- **Qualified `@security-reviewer` trigger**: the reviewer invocation
  entry for external integration suggests `@security-reviewer` only when
  the integration involves credentials, response parsing, or a new parser
  dependency — not for every external GET. This keeps the signal targeted
  (no noise on trivial read-only public API calls) while remaining
  consistent with Guardrail 10, which explicitly lists "new external
  service integrations" and "new dependencies that process user input" as
  security review triggers
