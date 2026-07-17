# OpenCode Tooling

This directory contains the OpenCode agent, command, and skill definitions
for the Sentinel project. This README serves as a quick-reference catalog.

For details on how agents are triggered automatically, see the Guardrails
section in `AGENTS.md`.

## Primary Agents

Primary agents are the main interaction modes, switchable with the Tab key.
They are configured in `opencode.json`.

| Agent | Scope | Permissions |
|-------|-------|-------------|
| **Plan** | Analysis and planning | Read-only (built-in) |
| **Spec** | Specifications and declarative configuration | Edit: `docs/**`, `AGENTS.md`, `.opencode/**`, `opencode.json` |
| **Code** | Implementation, tests, CI/CD, infrastructure | Edit: all files (`docs/**` requires confirmation) |

- **Plan** — read-only mode for analysis, planning, and discussion without
  making changes. Uses the OpenCode built-in Plan agent.
- **Spec** — writes and maintains feature specifications, data model, API
  spec, conventions, agent definitions, and all declarative project
  configuration. Cannot modify implementation code. Prompt:
  `.opencode/prompts/spec.md`
- **Code** — implements features from specifications, writes tests, and
  maintains all executable artifacts. Must signal spec gaps and obtain user
  approval before making design decisions. Prompt:
  `.opencode/prompts/code.md`

## Subagents

All subagents are defined in `.opencode/agents/`. Unless noted otherwise,
subagents are **read-only reviewers** that analyze code or specifications
and report findings without modifying files.

| Agent | Type | Trigger | Purpose |
|-------|------|---------|---------|
| `@api-convention-reviewer` | Reviewer | Guardrail 20 | Verifies API endpoint definitions in specs conform to project conventions |
| `@api-parity-reviewer` | Reviewer | Guardrail 12 | Verifies the REST API exposes all operations defined in feature specifications |
| `@cicd` | Implementation | Guardrail 5 | CI/CD pipeline expert for GitHub Actions, Dockerfiles, and deployment configs |
| `@data-model-reviewer` | Reviewer | Guardrail 8 | Reviews data model changes for simplicity, consistency, and conventions |
| `@design-reviewer` | Reviewer | On-demand | Evaluates architectural decisions, complexity, and alternatives in feature specs |
| `@docs-placement-reviewer` | Reviewer | Guardrail 21 | Verifies rules and patterns are placed in the most appropriate location (not misplaced or duplicated) |
| `@docs-reviewer` | Reviewer | Guardrail 9 | Reviews documentation completeness and coherence with implementation |
| `@external-contract-verifier` | Reviewer | On-demand | Verifies external service request/response structures match real upstream contracts |
| `@fetcher-compliance-reviewer` | Reviewer | Guardrail 14 | Verifies fetchers inherit from BaseFetcher (or BaseCVEFetcher for CVE fetchers), report metrics correctly, and exclude `SoftTimeLimitExceeded` from per-item catches |
| `@security-reviewer` | Reviewer | Guardrail 10 | Reviews code for security vulnerabilities and insecure patterns |
| `@spec-coherence-reviewer` | Reviewer | Guardrail 15 | Detects contradictions and inconsistencies across feature specifications |
| `@spec-gap-analyzer` | Reviewer | Guardrail 17 | Identifies uncovered functional cases and missing edge-case handling in specs |
| `@test-reviewer` | Reviewer | Guardrail 6 | Reviews test quality, coverage, and adherence to testing conventions |
| `@identity-integrity-reviewer` | Reviewer | Guardrail 11 | Verifies IdentityAuditEvent audit trail compliance and detail JSONB schema completeness for identity mutations |
| `@ticket-integrity-reviewer` | Reviewer | Guardrail 11 | Verifies TicketAuditEvent audit trail and ticket_mutations module compliance |

## Commands

Commands are defined in `.opencode/commands/` and invoked with `/command-name`.

| Command | Purpose |
|---------|---------|
| `/check-spec` | Verify that implementation code conforms to its feature specification |
| `/idea` | Add a new idea to the brainstorming list in `docs/drafts/ideas.md` |
| `/review-spec` | Interactive spec review and finding resolution workflow |
| `/run-tests` | Run the full test suite (backend tests and linting) |

## Skills

Skills are defined in `.opencode/skills/` and provide guided, multi-step
workflows for common tasks. They are loaded automatically when a task matches
their description.

| Skill | Purpose |
|-------|---------|
| `new-api-endpoint` | Guided workflow for creating a new API endpoint with proper schema, service layer, and tests |
| `new-feature` | Guided workflow for adding a new feature following the spec-first approach |

## Directory Structure

```
.opencode/
├── agents/           # Subagent definitions (one .md file per agent)
├── commands/         # Slash command definitions
├── prompts/          # Primary agent prompt files
│   ├── spec.md       # Spec agent instructions
│   └── code.md       # Code agent instructions
├── skills/           # Multi-step workflow definitions
├── package.json      # Plugin dependency (@opencode-ai/plugin)
└── README.md         # This file
```
