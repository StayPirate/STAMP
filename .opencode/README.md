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

All reviewer agents apply the proportionality filter in `AGENTS.md`
Guardrail 26 before reporting findings. Speculative, unnecessary,
over-documenting, or disproportionate findings are omitted rather than turned
into project requirements.

| Agent | Type | Trigger | Purpose |
|-------|------|---------|---------|
| `@api-convention-reviewer` | Reviewer | Guardrail 20 | Verifies API endpoint definitions in specs conform to project conventions |
| `@api-parity-reviewer` | Reviewer | Guardrail 12 | Verifies the REST API exposes all operations defined in feature specifications |
| `@cicd` | Implementation | Guardrail 5 | CI/CD pipeline expert for GitHub Actions, Dockerfiles, and deployment configs |
| `@data-model-reviewer` | Reviewer | Guardrail 8 | Reviews data model changes for simplicity, consistency, and conventions |
| `@design-reviewer` | Reviewer | Guardrail 26 | Applies a simplicity-first review to new or substantially modified feature specs |
| `@docs-placement-reviewer` | Reviewer | Guardrail 21 | Verifies rules and patterns are placed in the most appropriate location (not misplaced or duplicated) |
| `@docs-reviewer` | Reviewer | Guardrail 9 | Reviews documentation completeness and coherence with implementation |
| `@external-contract-verifier` | Reviewer | On-demand | Verifies external service request/response structures match real upstream contracts |
| `@fetcher-compliance-reviewer` | Reviewer | Guardrail 14 | Verifies fetchers inherit from BaseFetcher (or BaseCVEFetcher for CVE fetchers), report metrics correctly, and exclude `SoftTimeLimitExceeded` from per-item catches |
| `@security-reviewer` | Reviewer | Guardrail 10 | Reviews code for security vulnerabilities and insecure patterns |
| `@spec-coherence-reviewer` | Reviewer | Guardrail 15 | Detects contradictions and inconsistencies across feature specifications |
| `@spec-conformance-reviewer` | Reviewer | Pre-PR (unconditional) | Verifies a pull request implements what its issue and owning specs require, and introduces no unspecified behavior |
| `@spec-gap-analyzer` | Reviewer | Guardrail 17 | Identifies uncovered functional cases and missing edge-case handling in specs |
| `@test-reviewer` | Reviewer | Guardrail 6 | Reviews test quality, coverage, audit trail assertions, and adherence to testing conventions |
| `@identity-integrity-reviewer` | Reviewer | Guardrail 11 | Verifies IdentityAuditEvent audit trail compliance and detail JSONB schema completeness for identity mutations |
| `@ticket-integrity-reviewer` | Reviewer | Guardrail 11 | Verifies TicketAuditEvent audit trail and ticket_mutations module compliance |

`@spec-conformance-reviewer` is the only subagent whose trigger is a moment
rather than a kind of change: it runs on every pull request, before the pull
request is opened or marked ready. It can also be invoked manually with an
explicit pull request reference, including on closed pull requests. It is not
yet backed by a guardrail — it is under calibration, and its invocation is
declared in `.opencode/prompts/code.md` and `.opencode/prompts/spec.md`.

### Model Tiering

Subagents have no `model` pinned by default: they inherit the model of the
primary agent that invoked them (`spec` or `code`, see Primary Agents above).
Subagents whose task involves deep analytical reasoning — finding subtle
security flaws, evaluating architectural complexity, discovering unspecified
scenarios, or reconciling nuanced cross-document/cross-diff detail — are
pinned to a more capable model with extended thinking enabled, since GitHub
Copilot prices all Claude Opus versions identically per token regardless of
version. All other subagents keep the default inherited-model behavior,
since their checks are comparatively structural and do not show the same
sensitivity to reasoning depth.

| Tier | Model | Agents |
|------|-------|--------|
| 1 (pinned) | `github-copilot/claude-opus-5`, extended thinking (`budgetTokens: 32000`) | `@security-reviewer`, `@design-reviewer`, `@spec-gap-analyzer`, `@spec-conformance-reviewer`, `@spec-coherence-reviewer` |
| 2 (inherited) | Invoking primary agent's model | All other subagents |

## Commands

Commands are defined in `.opencode/commands/` and invoked with `/command-name`.

| Command | Purpose |
|---------|---------|
| `/idea` | Add a new idea to the brainstorming list in `docs/drafts/ideas.md` |
| `/review-spec` | Interactive spec review and finding resolution workflow |

## Skills

Skills are defined in `.opencode/skills/` and provide guided, multi-step
workflows for common tasks. They are loaded automatically when a task matches
their description.

| Skill | Purpose |
|-------|---------|
| `new-api-endpoint` | Guided workflow for adding/modifying an endpoint in an existing feature (schema, service, thin route, tests, and reviews) |

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
